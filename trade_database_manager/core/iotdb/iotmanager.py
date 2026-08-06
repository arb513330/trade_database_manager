# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : iotmanager.py
# @Purpose : Manage Apache IoTDB (table model) operations via the apache-iotdb SDK

from collections.abc import Container, Sequence
from datetime import datetime

import numpy as np
import pandas as pd

from iotdb.table_session import TableSession, TableSessionConfig
from iotdb.utils.BitMap import BitMap
from iotdb.utils.IoTDBConstants import TSDataType
from iotdb.utils.NumpyTablet import NumpyTablet
from iotdb.utils.Tablet import ColumnType
from iotdb.utils.exception import IoTDBConnectionException

from ...config import CONFIG
from .utils import (
    escape_string,
    format_time_literal,
    infer_iotdb_type,
    to_iotdb_value,
    translate_agg_func,
)


def _column_ts_type(series: pd.Series) -> TSDataType:
    """Pick the tablet TSDataType for a pandas column."""
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return TSDataType.BOOLEAN
    if pd.api.types.is_integer_dtype(dtype):
        return TSDataType.INT64
    if pd.api.types.is_float_dtype(dtype):
        return TSDataType.DOUBLE
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return TSDataType.TIMESTAMP
    return TSDataType.STRING


class IoTManager:
    """IoTDB (table model) manager, drop-in equivalent of QuestManager.

    Uses a single persistent ``TableSession`` (lazy, reconnect-on-failure).
    All tables are qualified with the configured database, e.g. ``tradedata.FUT_1m``.
    """

    def __init__(self):
        self._host = CONFIG.get("iotdb_host", "localhost")
        self._port = CONFIG.get("iotdb_port", 6667)
        self._username = CONFIG.get("iotdb_user", "root")
        self._password = CONFIG.get("iotdb_password", "root")
        self._database = CONFIG.get("iotdb_database", "tradedata")

        self._session: TableSession | None = None

    # -------------------------------------------------------------- connection

    def _q(self, table_name: str) -> str:
        return f"{self._database}.{table_name}"

    def _connect(self) -> None:
        cfg = TableSessionConfig(
            node_urls=[f"{self._host}:{self._port}"],
            username=self._username,
            password=self._password,
            database=self._database,
        )
        self._session = TableSession(cfg)

    def _ensure_connected(self) -> None:
        if self._session is None:
            self._connect()

    def _with_reconnect(self, fn):
        self._ensure_connected()
        try:
            return fn()
        except (IoTDBConnectionException, OSError):
            self._connect()
            return fn()

    def _execute_sql(self, sql: str) -> None:
        self._with_reconnect(lambda: self._session.execute_non_query_statement(sql))

    def _execute_query(self, sql: str) -> pd.DataFrame:
        def run() -> pd.DataFrame:
            ds = self._session.execute_query_statement(sql)
            try:
                return self._normalize_tz(ds.todf())
            finally:
                ds.close_operation_handle()
        return self._with_reconnect(run)

    @staticmethod
    def _normalize_tz(df: pd.DataFrame) -> pd.DataFrame:
        """Strip timezone info from returned timestamp columns.

        QuestDB returns naive timestamps; IoTDB returns session-timezone-aware
        ones (e.g. +08:00). Stripping keeps the IoTManager a drop-in.
        Iterates positionally so duplicate column names (joins) are handled.
        """
        for i in range(df.shape[1]):
            ser = df.iloc[:, i]
            if len(ser) and isinstance(ser.iloc[0], pd.Timestamp) and ser.iloc[0].tzinfo is not None:
                df.isetitem(i, pd.to_datetime(ser).dt.tz_localize(None))
        return df

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _build_where(filter_fields) -> str:
        """Build an AND-joined WHERE condition string from a filter dict."""
        if not filter_fields:
            return ""
        conditions = []
        for field, value in filter_fields.items():
            if isinstance(value, str):
                conditions.append(f"{field} = '{escape_string(value)}'")
            elif isinstance(value, Container) and not isinstance(value, (str, bytes)):
                formatted = ", ".join(
                    f"'{escape_string(v)}'" if isinstance(v, str) else str(v)
                    for v in value
                )
                conditions.append(f"{field} IN ({formatted})")
            elif isinstance(value, (pd.Timestamp, np.datetime64, datetime)):
                conditions.append(f"{field} = {format_time_literal(value)}")
            elif isinstance(value, (bool, np.bool_)):
                conditions.append(f"{field} = {'TRUE' if value else 'FALSE'}")
            else:
                conditions.append(f"{field} = {value}")
        return " AND ".join(conditions)

    def _build_create_sql(
        self,
        table_name: str,
        columns: list[tuple[str, type]],
        designated_timestamp: str,
        tag_set: set,
    ) -> str:
        col_defs = []
        for name, py_type in columns:
            if name == designated_timestamp:
                col_defs.append(f"    {name} TIMESTAMP TIME")
            elif name in tag_set:
                col_defs.append(f"    {name} STRING TAG")
            else:
                col_defs.append(f"    {name} {infer_iotdb_type(py_type)} FIELD")
        sql = f"CREATE TABLE IF NOT EXISTS {self._q(table_name)} (\n"
        sql += ",\n".join(col_defs)
        sql += "\n)"
        return sql

    def _build_insert_sql(self, table_name: str, df: pd.DataFrame, designated_timestamp: str) -> str:
        cols = list(df.columns)
        col_list = ", ".join(cols)
        rows = []
        for row in df.itertuples(index=False):
            vals = []
            for v in row:
                v = to_iotdb_value(v)
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, str):
                    vals.append(f"'{escape_string(v)}'")
                elif isinstance(v, datetime):
                    vals.append(format_time_literal(v))
                elif isinstance(v, bool):
                    vals.append("TRUE" if v else "FALSE")
                else:
                    vals.append(str(v))
            rows.append("(" + ", ".join(vals) + ")")
        return f"INSERT INTO {self._q(table_name)} ({col_list}) VALUES " + ", ".join(rows)

    def _build_tablets(
        self,
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str,
        symbols: list[str],
    ) -> list[NumpyTablet]:
        """Chunk df by symbol and build one NumpyTablet per group (table model allows
        mixed tags in one tablet, but per-symbol chunks keep memory bounded)."""
        fields = [c for c in df.columns if c != designated_timestamp and c not in symbols]
        target = self._q(table_name)
        tablets = []
        for _, grp in df.groupby(symbols) if symbols else [("__all__", df)]:
            timestamps = pd.to_datetime(grp[designated_timestamp]).values.astype("int64") // 10**6
            column_names = symbols + fields
            data_types: list = []
            values: list = []
            bitmaps: list = []
            column_types: list = []
            for col in symbols:
                data_types.append(TSDataType.STRING)
                values.append(grp[col].astype(str).to_numpy())
                column_types.append(ColumnType.TAG)
                bitmaps.append(BitMap(len(grp)))
            for col in fields:
                ser = grp[col]
                t = _column_ts_type(ser)
                bm = BitMap(len(grp))
                if t == TSDataType.DOUBLE:
                    arr = ser.to_numpy(dtype="float64").copy()
                    for i in range(len(arr)):
                        if np.isnan(arr[i]):
                            bm.mark(i)
                            arr[i] = 0.0
                    values.append(arr)
                elif t == TSDataType.INT64:
                    arr = ser.to_numpy(dtype="float64").copy()
                    for i in range(len(arr)):
                        if np.isnan(arr[i]):
                            bm.mark(i)
                            arr[i] = 0.0
                    values.append(arr.astype("int64"))
                elif t == TSDataType.TIMESTAMP:
                    ser_t = pd.to_datetime(ser)
                    arr = ser_t.values.astype("int64") // 10**6
                    for i, v in enumerate(ser_t):
                        if pd.isna(v):
                            bm.mark(i)
                            arr[i] = 0
                    values.append(arr)
                elif t == TSDataType.BOOLEAN:
                    values.append(ser.to_numpy(dtype=bool))
                else:
                    values.append(ser.astype(str).to_numpy())
                data_types.append(t)
                column_types.append(ColumnType.FIELD)
                bitmaps.append(bm)
            tablets.append(
                NumpyTablet(
                    target,
                    column_names,
                    data_types,
                    values,
                    timestamps,
                    bitmaps=bitmaps,
                    column_types=column_types,
                )
            )
        return tablets

    # ------------------------------------------------------------------ schema

    def table_exists(self, table_name: str) -> bool:
        try:
            self._execute_query(f"DESCRIBE {self._q(table_name)}")  # [VERIFY-ON-SERVER]
            return True
        except Exception:
            return False

    def create_table(
        self,
        table_name: str,
        columns: list[tuple[str, type]],
        designated_timestamp: str = "timestamp",
        partition_by: str = "DAY",
        wal: bool = True,
        dedup_keys: list[str] = None,
        symbol_columns: list[str] = None,
        indexed_columns: list[str] = None,
    ) -> None:
        # partition_by / wal have no IoTDB table-model equivalent: IoTDB always
        # WALs and auto-partitions by time + device. Accepted for signature parity.
        tag_set = (
            set(symbol_columns or [])
            | set(indexed_columns or [])
            | set(dedup_keys or [])
        )
        self._execute_sql(self._build_create_sql(table_name, columns, designated_timestamp, tag_set))

    def insert_column(self, table_name: str, column_name: str, column_type: str):
        self._execute_sql(
            f"ALTER TABLE {self._q(table_name)} ADD COLUMN {column_name} {column_type} FIELD"
        )

    def delete_column(self, table_name: str, column_name: str):
        self._execute_sql(f"ALTER TABLE {self._q(table_name)} DROP COLUMN {column_name}")

    def rename_column(self, table_name: str, old_name: str, new_name: str):
        # Verified against the live server: the table model does not support
        # ALTER TABLE ... RENAME COLUMN ("renaming for base table column is
        # currently unsupported"). Fail clearly instead of a raw server error.
        raise NotImplementedError(
            "IoTDB table model does not support renaming columns"
        )

    # ------------------------------------------------------------------- write

    def insert(
        self,
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str = "timestamp",
        symbol_columns: list[str] = None,
        use_ilp: bool | None = None,
    ) -> int:
        if df is None or df.empty:
            return 0
        if use_ilp is None:
            use_ilp = len(df) > 1000
        if use_ilp:
            return self._insert_tablet(table_name, df, designated_timestamp, symbol_columns)
        return self._insert_sql(table_name, df, designated_timestamp)

    def _insert_sql(self, table_name: str, df: pd.DataFrame, designated_timestamp: str) -> int:
        self._execute_sql(self._build_insert_sql(table_name, df, designated_timestamp))  # [VERIFY-ON-SERVER]
        return len(df)

    def _insert_tablet(
        self,
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str,
        symbol_columns: list[str],
    ) -> int:
        symbols = list(symbol_columns or [])
        tablets = self._build_tablets(table_name, df, designated_timestamp, symbols)
        n = 0
        for tablet in tablets:
            self._with_reconnect(lambda tb=tablet: self._session.insert(tb))
            n += tablet.get_row_number()
        return n

    # -------------------------------------------------------------------- read

    def read_data(
        self,
        table_name: str,
        query_fields="*",
        filter_fields=None,
        unique: bool = False,
    ) -> pd.DataFrame:
        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        sql = f"SELECT {'DISTINCT ' if unique else ''}{fields} FROM {self._q(table_name)}"
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
        ts_col = time_column or "timestamp"
        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        sql = f"SELECT {fields} FROM {self._q(table_name)}"
        conditions = []
        if start_time is not None:
            conditions.append(f"{ts_col} >= {format_time_literal(start_time)}")
        if end_time is not None:
            conditions.append(f"{ts_col} <= {format_time_literal(end_time)}")
        filter_where = self._build_where(filter_fields)
        if filter_where:
            conditions.append(filter_where)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if sample_by:
            # No aggregations are known here, so time-bucketing is not possible
            # on the table model; use sample_by() for explicit aggregations.
            import warnings

            warnings.warn(
                "sample_by in read_range_data is not supported on IoTDB; use sample_by() instead"
            )
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

        t0, t1 = table_names[0], table_names[1]

        def table_where(tn: str) -> str:
            w = self._build_where((filter_fields or {}).get(tn))
            return f" WHERE {w}" if w else ""

        if join_type == "asof":
            # IoTDB table model has no ASOF JOIN; faithful fallback via merge_asof.

            def asof_select(tn: str) -> str:
                if query_fields == "*":
                    return "*"
                if isinstance(query_fields, dict):
                    cols = query_fields.get(tn)
                    cols = [cols] if isinstance(cols, str) else list(cols or [])
                elif isinstance(query_fields, list):
                    cols = list(query_fields)
                else:
                    cols = []
                need = [c for c in join_columns if c not in cols]
                return ", ".join(cols + need) if (cols or need) else "*"

            df0 = self._execute_query(f"SELECT {asof_select(t0)} FROM {self._q(t0)}{table_where(t0)}")
            df1 = self._execute_query(f"SELECT {asof_select(t1)} FROM {self._q(t1)}{table_where(t1)}")
            asof_col = join_columns[-1]
            by_cols = list(join_columns[:-1])
            kwargs: dict = {"direction": "backward"}
            if by_cols:
                kwargs["by"] = by_cols
            return pd.merge_asof(
                df0.sort_values(asof_col),
                df1.sort_values(asof_col),
                on=asof_col,
                **kwargs,
            )

        # Inner join: bare SELECT * is ambiguous when both tables share column
        # names on this server, so alias both tables and qualify the columns.
        if query_fields == "*":
            select_clause = "t0.*, t1.*"
        elif isinstance(query_fields, dict):
            parts = []
            for tn, cols in query_fields.items():
                alias = "t0" if tn == t0 else ("t1" if tn == t1 else tn)
                if isinstance(cols, str):
                    parts.append(f"{alias}.{cols}")
                else:
                    parts.extend(f"{alias}.{c}" for c in cols)
            select_clause = ", ".join(parts)
        else:
            select_clause = "t0.*, t1.*"

        join_cond = " AND ".join(f"t0.{c} = t1.{c}" for c in join_columns)
        sql = (
            f"SELECT {select_clause} FROM {self._q(t0)} t0 "
            f"INNER JOIN {self._q(t1)} t1 ON {join_cond}"
        )
        conditions = []
        for tn in table_names:
            w = self._build_where((filter_fields or {}).get(tn))
            if w:
                conditions.append(w)
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
            table_name, target_column, group_column, extremum_column, "DESC", filter_fields
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
            table_name, target_column, group_column, extremum_column, "ASC", filter_fields
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
        # CTE form: this server rejects an outer SELECT over a derived table
        # containing a window function, but accepts the equivalent CTE.
        sql = (
            f"WITH c AS ("
            f"  SELECT {col_list}, "
            f"    ROW_NUMBER() OVER (PARTITION BY {', '.join(gcols)} "
            f"      ORDER BY {extremum_column} {order}) AS rn"
            f"  FROM {self._q(table_name)}{where_clause}"
            f") SELECT {col_list} FROM c WHERE rn = 1"
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
        ts_col = timestamp_column or "timestamp"
        if not aggregations:
            # Can't GROUP BY without knowing which columns to aggregate.
            import warnings

            warnings.warn(
                "sample_by without aggregations is not supported on IoTDB; "
                "returning ungrouped rows"
            )
            sql = f"SELECT * FROM {self._q(table_name)}"
            where = self._build_where(filter_fields)
            if where:
                sql += f" WHERE {where}"
            return self._execute_query(sql)

        agg_exprs = [
            f"{translate_agg_func(func)}({col}) AS {col}"
            for col, func in aggregations.items()
        ]
        # Table-model time bucketing: date_bin(...) AS time + GROUP BY 1.
        select_clause = f"date_bin({interval}, {ts_col}) AS {ts_col}, " + ", ".join(agg_exprs)
        sql = f"SELECT {select_clause} FROM {self._q(table_name)}"
        where = self._build_where(filter_fields)
        if where:
            sql += f" WHERE {where}"
        sql += " GROUP BY 1"
        if fill:
            # QuestDB FILL(...) has no guaranteed table-model equivalent across
            # versions. Round 1: explicit no-op (unfilled buckets), not silent.
            import warnings

            warnings.warn("sample_by(fill=...) is not yet supported; returning unfilled buckets")
        return self._execute_query(sql)

    def latest_on(
        self,
        table_name: str,
        partition_by,
        timestamp_column: str = None,
        filter_fields=None,
        query_fields="*",
        search_start=None,
    ) -> pd.DataFrame:
        ts_col = timestamp_column or "timestamp"
        partition_cols = partition_by if isinstance(partition_by, list) else [partition_by]
        partition_sql = ", ".join(partition_cols)

        # The server rejects an outer SELECT over a window-function derived
        # table, so latest-per-partition is computed via a MAX(timestamp) join
        # (a.* needs no schema knowledge; verified against the live server).
        if query_fields == "*":
            select_clause = "a.*"
        else:
            cols = [query_fields] if isinstance(query_fields, str) else list(query_fields)
            select_clause = ", ".join(f"a.{c}" for c in cols)

        conditions = []
        if search_start is not None:
            conditions.append(f"{ts_col} >= {format_time_literal(search_start)}")
        filter_where = self._build_where(filter_fields)
        if filter_where:
            conditions.append(filter_where)
        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        join_cond = " AND ".join(f"a.{c} = b.{c}" for c in partition_cols)
        sql = (
            f"SELECT {select_clause} FROM {self._q(table_name)} a"
            f" INNER JOIN ("
            f"  SELECT {partition_sql}, MAX({ts_col}) AS __max_ts"
            f"  FROM {self._q(table_name)}{where_clause}"
            f"  GROUP BY {partition_sql}"
            f" ) b ON {join_cond} AND a.{ts_col} = b.__max_ts"
        )
        return self._execute_query(sql)
