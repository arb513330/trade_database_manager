# @Time    : 2026/7/11 19:00
# @Author  : YQ Tsui
# @File    : questmanager.py
# @Purpose : Manage QuestDB operations via PG wire + ILP

from collections.abc import Container, Sequence
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
import psycopg
from questdb.ingress import Sender, IngressError

from ...config import CONFIG
from ..typedefs import FILTERFIELD_TYPE, QUERYFIELD_TYPE
from .utils import infer_questdb_type


class QuestManager:
    """
    QuestDB manager with dual connection: PG wire (psycopg) for SQL operations
    and ILP (questdb.ingress.Sender) for high-speed bulk inserts.

    :ivar str _host: QuestDB host address.
    :ivar int _pg_port: PostgreSQL wire port (default 8812).
    :ivar int _ilp_port: ILP port (default 9009).
    :ivar str _username: QuestDB username.
    :ivar str _password: QuestDB password.
    :ivar dict _table_meta: Cached table metadata (column info, designated timestamp).
    """

    def __init__(self):
        self._host = CONFIG.get("questdb_host", "localhost")
        self._pg_port = CONFIG.get("questdb_pg_port", 8812)
        self._ilp_port = CONFIG.get("questdb_ilp_port", 9009)
        self._username = CONFIG.get("questdb_user", "admin")
        self._password = CONFIG.get("questdb_password", "quest")
        self._table_meta: dict[str, dict] = {}

    def _get_conn(self) -> psycopg.Connection:
        return psycopg.connect(
            host=self._host,
            port=self._pg_port,
            user=self._username,
            password=self._password,
            dbname="qdb",
            autocommit=True,
        )

    def _execute_sql(self, sql: str) -> list[tuple]:
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            try:
                return cur.fetchall()
            except psycopg.ProgrammingError:
                return []

    def _execute_query(self, sql: str) -> pd.DataFrame:
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            try:
                cols = [desc.name for desc in cur.description]
                rows = cur.fetchall()
                return pd.DataFrame(rows, columns=cols)
            except psycopg.ProgrammingError:
                return pd.DataFrame()

    def _load_table_meta(self, table_name: str) -> dict:
        if table_name not in self._table_meta:
            sql = (
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                f"WHERE table_name = '{table_name}'"
            )
            columns = self._execute_sql(sql)
            designated_ts = None
            try:
                row = self._execute_sql(
                    f"SELECT designatedTimestamp FROM tables() WHERE table_name = '{table_name}'"
                )
                if row:
                    designated_ts = row[0][0]
            except Exception:
                pass
            self._table_meta[table_name] = {
                "columns": columns,
                "designated_timestamp": designated_ts,
            }
        return self._table_meta[table_name]

    @staticmethod
    def _build_where(filter_fields) -> str:
        if not filter_fields:
            return ""
        conditions = []
        for field, value in filter_fields.items():
            if isinstance(value, str):
                conditions.append(f"{field} = '{value}'")
            elif isinstance(value, Container) and not isinstance(value, str | bytes):
                formatted = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in value)
                conditions.append(f"{field} IN ({formatted})")
            else:
                conditions.append(f"{field} = {value}")
        return " AND ".join(conditions)

    @staticmethod
    def _to_ilp_value(value: Any) -> Any:
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if pd.isna(value):
            return None
        return value

    def table_exists(self, table_name: str) -> bool:
        rows = self._execute_sql(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_name = '{table_name}'"
        )
        return len(rows) > 0

    def create_table(
        self,
        table_name: str,
        columns: list[tuple[str, type]],
        designated_timestamp: str = "timestamp",
        partition_by: str = "DAY",
        wal: bool = True,
        dedup_keys: list[str] = None,
        symbol_columns: list[str] = None,
    ):
        col_defs = []
        symbol_set = set(symbol_columns or [])
        for name, py_type in columns:
            is_sym = name in symbol_set
            qdb_type = infer_questdb_type(py_type, is_symbol=is_sym)
            col_defs.append(f"    {name} {qdb_type}")

        storage = f"TIMESTAMP({designated_timestamp}) PARTITION BY {partition_by}"
        if wal:
            storage += " WAL"
        if dedup_keys:
            storage += f" DEDUP UPSERT KEYS({', '.join(dedup_keys)})"

        sql = f"CREATE TABLE IF NOT EXISTS {table_name} (\n"
        sql += ",\n".join(col_defs)
        sql += f"\n) {storage}"
        self._execute_sql(sql)

    def insert_column(self, table_name: str, column_name: str, column_type: str):
        self._execute_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def delete_column(self, table_name: str, column_name: str):
        self._execute_sql(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")

    def rename_column(self, table_name: str, old_name: str, new_name: str):
        self._execute_sql(f"ALTER TABLE {table_name} RENAME COLUMN {old_name} TO {new_name}")

    def insert(
        self,
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str = "timestamp",
        symbol_columns: list[str] = None,
        use_ilp: bool | None = None,
    ) -> int:
        if use_ilp is None:
            use_ilp = len(df) > 1000
        if use_ilp:
            return self._insert_ilp(table_name, df, designated_timestamp, symbol_columns)
        return self._insert_sql(table_name, df)

    def _insert_ilp(
        self,
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str = "timestamp",
        symbol_columns: list[str] = None,
    ) -> int:
        symbols = symbol_columns or []
        n_rows = 0
        try:
            with Sender.from_conf(f"tcp::addr={self._host}:{self._ilp_port};username={self._username};password={self._password}") as sender:
                for _, row in df.iterrows():
                    row_symbols = {
                        col: str(row[col])
                        for col in symbols
                        if col in df.columns and not pd.isna(row[col])
                    }
                    row_columns = {
                        col: self._to_ilp_value(row[col])
                        for col in df.columns
                        if col != designated_timestamp
                        and col not in symbols
                        and not pd.isna(row[col])
                    }
                    ts = row[designated_timestamp]
                    if isinstance(ts, pd.Timestamp):
                        ts = ts.to_pydatetime()
                    elif isinstance(ts, np.datetime64):
                        ts = pd.Timestamp(ts).to_pydatetime()
                    sender.row(table_name, symbols=row_symbols, columns=row_columns, at=ts)
                    n_rows += 1
                sender.flush()
        except IngressError as exc:
            raise RuntimeError(
                f"ILP insert failed for {table_name} ({n_rows} of {len(df)} rows sent)"
            ) from exc
        return n_rows

    def _insert_sql(self, table_name: str, df: pd.DataFrame) -> int:
        cols = list(df.columns)
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        values = []
        for row in df.itertuples(index=False):
            row_vals = []
            for v in row:
                if pd.isna(v):
                    row_vals.append(None)
                elif isinstance(v, pd.Timestamp):
                    row_vals.append(v.to_pydatetime())
                else:
                    row_vals.append(v)
            values.append(tuple(row_vals))
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
                values,
            )
            return cur.rowcount if cur.rowcount >= 0 else len(values)

    def read_data(
        self,
        table_name: str,
        query_fields="*",
        filter_fields=None,
        unique: bool = False,
    ) -> pd.DataFrame:
        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        sql = f"SELECT {'DISTINCT ' if unique else ''}{fields} FROM {table_name}"
        where = self._build_where(filter_fields)
        if where:
            sql += f" WHERE {where}"
        return self._execute_query(sql)

    def read_range_data(
        self,
        table_name: str,
        query_fields="*",
        start_time=None,
        end_time=None,
        time_column: str = None,
        filter_fields=None,
        sample_by: str = None,
    ) -> pd.DataFrame:
        meta = self._load_table_meta(table_name)
        ts_col = time_column or meta.get("designated_timestamp", "timestamp")

        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        sql = f"SELECT {fields} FROM {table_name}"

        conditions = []
        if start_time is not None:
            conditions.append(f"{ts_col} >= '{start_time.isoformat()}'")
        if end_time is not None:
            conditions.append(f"{ts_col} <= '{end_time.isoformat()}'")

        filter_where = self._build_where(filter_fields)
        if filter_where:
            conditions.append(filter_where)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if sample_by:
            sql += f" SAMPLE BY {sample_by}"
        return self._execute_query(sql)

    def read_data_across_tables(
        self,
        table_names: Sequence[str],
        join_columns: Sequence[str],
        query_fields="*",
        filter_fields=None,
        join_type: str = "inner",
    ) -> pd.DataFrame:
        if len(table_names) < 2:
            raise ValueError("At least 2 table names are required")
        if join_type not in ("inner", "asof"):
            raise ValueError(f"Unsupported join_type: {join_type}")

        t0 = table_names[0]
        t1 = table_names[1]

        if query_fields == "*":
            select_clause = "*"
        elif isinstance(query_fields, dict):
            parts = []
            for tn, cols in query_fields.items():
                if isinstance(cols, str):
                    parts.append(f"{tn}.{cols}")
                else:
                    parts.extend(f"{tn}.{c}" for c in cols)
            select_clause = ", ".join(parts)
        else:
            select_clause = "*"

        if join_type == "asof":
            sql = (
                f"SELECT {select_clause} FROM {t0} "
                f"ASOF JOIN {t1} ON ({', '.join(join_columns)})"
            )
        else:
            join_cond = " AND ".join(f"{t0}.{c} = {t1}.{c}" for c in join_columns)
            sql = f"SELECT {select_clause} FROM {t0} INNER JOIN {t1} ON {join_cond}"

        if filter_fields:
            conditions = []
            for tn, ff in filter_fields.items():
                where = self._build_where(ff)
                if where:
                    conditions.append(where)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)

        return self._execute_query(sql)

    def read_max_in_group(
        self,
        table_name: str,
        target_column,
        group_column,
        extremum_column: str,
        filter_fields=None,
    ) -> pd.DataFrame:
        return self._read_extremum_in_group(
            table_name, target_column, group_column, extremum_column, "DESC", filter_fields,
        )

    def read_min_in_group(
        self,
        table_name: str,
        target_column,
        group_column,
        extremum_column: str,
        filter_fields=None,
    ) -> pd.DataFrame:
        return self._read_extremum_in_group(
            table_name, target_column, group_column, extremum_column, "ASC", filter_fields,
        )

    def _read_extremum_in_group(
        self,
        table_name: str,
        target_column,
        group_column,
        extremum_column: str,
        order: str,
        filter_fields=None,
    ) -> pd.DataFrame:
        tcols = [target_column] if isinstance(target_column, str) else target_column
        gcols = [group_column] if isinstance(group_column, str) else group_column
        all_cols = list(dict.fromkeys(gcols + tcols))
        col_list = ", ".join(all_cols)

        where = self._build_where(filter_fields)
        where_clause = f" WHERE {where}" if where else ""

        sql = (
            f"SELECT {col_list} FROM ("
            f"  SELECT {col_list}, "
            f"    ROW_NUMBER() OVER (PARTITION BY {', '.join(gcols)} "
            f"      ORDER BY {extremum_column} {order}) AS rn"
            f"  FROM {table_name}{where_clause}"
            f") WHERE rn = 1"
        )
        return self._execute_query(sql)

    def sample_by(
        self,
        table_name: str,
        interval: str = "1m",
        aggregations: dict[str, str] = None,
        timestamp_column: str = None,
        filter_fields=None,
        fill: str = None,
        align_to_calendar: bool = True,
    ) -> pd.DataFrame:
        meta = self._load_table_meta(table_name)
        ts_col = timestamp_column or meta.get("designated_timestamp", "timestamp")

        if aggregations:
            agg_exprs = [f"{func}({col}) AS {col}" for col, func in aggregations.items()]
            select_clause = f"{ts_col}, " + ", ".join(agg_exprs)
        else:
            select_clause = "*"

        sql = f"SELECT {select_clause} FROM {table_name}"
        where = self._build_where(filter_fields)
        if where:
            sql += f" WHERE {where}"

        sql += f" SAMPLE BY {interval}"
        if align_to_calendar:
            sql += " ALIGN TO CALENDAR"
        if fill:
            sql += f" FILL({fill})"
        return self._execute_query(sql)

    def latest_on(
        self,
        table_name: str,
        partition_by,
        timestamp_column: str = None,
        filter_fields=None,
    ) -> pd.DataFrame:
        meta = self._load_table_meta(table_name)
        ts_col = timestamp_column or meta.get("designated_timestamp", "timestamp")

        partition = ", ".join(partition_by) if isinstance(partition_by, list) else partition_by
        sql = f"SELECT * FROM {table_name}"
        where = self._build_where(filter_fields)
        if where:
            sql += f" WHERE {where}"
        sql += f" LATEST ON {ts_col} PARTITION BY {partition}"
        return self._execute_query(sql)
