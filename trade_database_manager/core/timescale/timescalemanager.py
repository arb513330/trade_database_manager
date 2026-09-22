# @Time    : 2026/9/14
# @Author  : YQ Tsui
# @File    : timescalemanager.py
# @Purpose : Manage time-series data in TimescaleDB using PostgreSQL semantics

import importlib.util
import re
import warnings
from collections.abc import Container, Sequence
from datetime import datetime
from functools import partial
from uuid import uuid4

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert

from ...config import CONFIG
from .utils import infer_timescale_type, normalize_timescale_interval, translate_agg_func


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe identifier: {name!r}")


def _insert_on_conflict_update(table, conn, keys, data_iter, indexes):
    """Pandas ``to_sql`` callback using PostgreSQL ON CONFLICT DO UPDATE."""
    data = [dict(zip(keys, row, strict=False)) for row in data_iter]
    stmt = insert(table.table).values(data)
    stmt = stmt.on_conflict_do_update(
        index_elements=indexes,
        set_={key: getattr(stmt.excluded, key) for key in keys},
    )
    result = conn.execute(stmt)
    return result.rowcount


class TimescaleManager:
    """TimescaleDB manager compatible with ``IoTManager``'s public interface.

    Tables live in a configurable schema (default ``tseries``) inside the same
    PostgreSQL instance used by ``SqlManager``. Timestamps are stored as local
    naive ``TIMESTAMP WITHOUT TIME ZONE`` values; callers are responsible for
    interpreting them with the appropriate metadata timezone.
    """

    def __init__(self):
        self._host = CONFIG.get("sqlhost", "localhost")
        self._port = CONFIG.get("sqlport", "5432")
        self._username = CONFIG.get("username")
        self._password = CONFIG.get("password")
        self._database = CONFIG.get("sqldbname")
        self._schema = CONFIG.get("timescale_schema", "tseries")
        self._environment_ready = False
        self._table_exists_cache: dict[str, bool] = {}

        sql_protocol = "postgresql" if importlib.util.find_spec("psycopg") is None else "postgresql+psycopg"
        connection_string = (
            f"{sql_protocol}://{self._username}:{self._password}@{self._host}:{self._port}/{self._database}"
        )
        self._engine = create_engine(connection_string)

    # ------------------------------------------------------------- SQL basics

    def _q(self, table_name: str) -> str:
        _check_identifier(table_name)
        _check_identifier(self._schema)
        return f'"{self._schema}"."{table_name}"'

    @staticmethod
    def _col(field: str) -> str:
        _check_identifier(field)
        return f'"{field}"'

    @staticmethod
    def _col_ref(field: str, prefix: str | None = None) -> str:
        _check_identifier(field)
        return f'{prefix}."{field}"' if prefix else f'"{field}"'

    def _ensure_environment(self) -> None:
        if self._environment_ready:
            return
        _check_identifier(self._schema)
        with self._engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"'))
        self._environment_ready = True

    def _execute(self, sql: str, params: dict | None = None):
        with self._engine.begin() as conn:
            return conn.execute(text(sql), params or {})

    def _read_sql(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        with self._engine.connect() as conn:
            return pd.read_sql_query(text(sql), conn, params=params or {})

    @staticmethod
    def _normalize_scalar(value):
        if isinstance(value, (np.integer, np.floating, np.bool_)):
            return value.item()
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, np.datetime64):
            return pd.Timestamp(value).to_pydatetime()
        try:
            if value is not None and pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return value

    def _build_where(
        self,
        filter_fields,
        prefix: str | None = None,
        params: dict | None = None,
    ) -> tuple[str, dict]:
        """Build an AND-joined parameterized WHERE clause from a filter dict."""
        if params is None:
            params = {}
        if not filter_fields:
            return "", params

        clauses = []
        for field, value in filter_fields.items():
            col = self._col_ref(field, prefix)
            if isinstance(value, Container) and not isinstance(value, (str, bytes)):
                values = [self._normalize_scalar(v) for v in value]
                if not values:
                    clauses.append("FALSE")
                    continue
                placeholders = []
                for item in values:
                    key = f"p{len(params)}"
                    params[key] = item
                    placeholders.append(f":{key}")
                clauses.append(f"{col} IN ({', '.join(placeholders)})")
            else:
                key = f"p{len(params)}"
                params[key] = self._normalize_scalar(value)
                clauses.append(f"{col} = :{key}")
        return " AND ".join(clauses), params

    def _select_fields(self, query_fields, prefix: str | None = None) -> str:
        if query_fields == "*":
            return f"{prefix}.*" if prefix else "*"
        if isinstance(query_fields, str):
            columns = [query_fields]
        else:
            columns = list(query_fields)
        if not columns:
            raise ValueError("query_fields must not be empty")
        return ", ".join(self._col_ref(col, prefix) for col in columns)

    def _table_columns(self, table_name: str) -> list[str]:
        self._ensure_environment()
        sql = (
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table_name "
            "ORDER BY ordinal_position"
        )
        df = self._read_sql(sql, {"schema": self._schema, "table_name": table_name})
        return df["column_name"].astype(str).tolist()

    def _cached_table_exists(self, table_name: str) -> bool:
        key = (self._schema, table_name)
        if key not in self._table_exists_cache:
            self._table_exists_cache[key] = self.table_exists(table_name)
        return self._table_exists_cache[key]

    # ------------------------------------------------------------------ schema

    def table_exists(self, table_name: str) -> bool:
        """Return whether ``table_name`` exists in the configured schema."""
        _check_identifier(table_name)
        self._ensure_environment()
        sql = (
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = :schema AND table_name = :table_name"
            ") AS table_exists"
        )
        df = self._read_sql(sql, {"schema": self._schema, "table_name": table_name})
        return bool(df.iloc[0, 0])

    def ensure_index(
        self,
        table_name: str,
        columns: Sequence[str],
        name: str | None = None,
        order_by: dict[str, str] | None = None,
    ) -> None:
        """Create a single-column or composite index if it does not already exist."""
        _check_identifier(table_name)
        self._ensure_environment()
        cols = [str(column) for column in columns]
        for column in cols:
            _check_identifier(column)

        if order_by is None:
            order_by = {column: "ASC" for column in cols}
        index_cols_sql = ", ".join(
            f"{self._col(column)} {order_by.get(column, 'ASC')}" for column in cols
        )

        index_name = name or f"idx_{table_name}_{'_'.join(cols)}"
        if len(cols) > 1:
            suffix = "_" + "_".join(order_by.get(column, "ASC").lower() for column in cols)
            index_name = f"idx_{table_name}_{'_'.join(cols)}{suffix}"
        self._execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON {self._q(table_name)} ({index_cols_sql})')

    def create_table(
        self,
        table_name: str,
        columns: list[tuple[str, type]],
        designated_timestamp: str = "timestamp",
        dedup_keys: list[str] = None,
        symbol_columns: list[str] = None,
        indexed_columns: list[str] = None,
    ) -> None:
        _check_identifier(table_name)
        _check_identifier(designated_timestamp)
        self._ensure_environment()

        column_names = [name for name, _ in columns]
        if designated_timestamp not in column_names:
            raise ValueError(f"designated_timestamp column {designated_timestamp!r} not in columns")

        key_cols = list(dict.fromkeys([designated_timestamp] + list(dedup_keys or [])))
        definitions = []
        for name, py_type in columns:
            _check_identifier(name)
            if name == designated_timestamp:
                type_sql = "TIMESTAMP WITHOUT TIME ZONE"
            else:
                type_sql = infer_timescale_type(py_type)
            not_null = " NOT NULL" if name in key_cols else ""
            definitions.append(f'    "{name}" {type_sql}{not_null}')

        primary_key = ", ".join(f'"{name}"' for name in key_cols)
        sql = f"CREATE TABLE IF NOT EXISTS {self._q(table_name)} (\n"
        sql += ",\n".join(definitions)
        sql += f",\n    PRIMARY KEY ({primary_key})\n)"
        self._execute(sql)

        hypertable_sql = (
            f'SELECT create_hypertable(\'"{self._schema}"."{table_name}"\', '
            f"'{designated_timestamp}', if_not_exists => TRUE)"
        )
        self._execute(hypertable_sql)

        for column in set(symbol_columns or []) | set(indexed_columns or []):
            _check_identifier(column)
            index_name = f"idx_{table_name}_{column}"
            self._execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON {self._q(table_name)} ({self._col(column)})')

        if "full_symbol" in column_names and designated_timestamp in column_names:
            self.ensure_index(
                table_name,
                ["full_symbol", designated_timestamp],
                order_by={"full_symbol": "ASC", designated_timestamp: "DESC"},
            )

    def add_columns(self, table_name: str, columns: Sequence[tuple[str, type]]) -> None:
        _check_identifier(table_name)
        self._ensure_environment()
        for column_name, py_type in columns:
            _check_identifier(column_name)
            column_type = infer_timescale_type(py_type)
            self._execute(
                f"ALTER TABLE {self._q(table_name)} ADD COLUMN IF NOT EXISTS {self._col(column_name)} {column_type}"
            )

    def insert_column(self, table_name: str, column_name: str, column_type: str):
        _check_identifier(table_name)
        _check_identifier(column_name)
        self._ensure_environment()
        self._execute(
            f"ALTER TABLE {self._q(table_name)} ADD COLUMN IF NOT EXISTS {self._col(column_name)} {column_type}"
        )

    def delete_column(self, table_name: str, column_name: str):
        _check_identifier(table_name)
        _check_identifier(column_name)
        self._ensure_environment()
        self._execute(f"ALTER TABLE {self._q(table_name)} DROP COLUMN IF EXISTS {self._col(column_name)}")

    def rename_column(self, table_name: str, old_name: str, new_name: str):
        _check_identifier(table_name)
        _check_identifier(old_name)
        _check_identifier(new_name)
        self._ensure_environment()
        self._execute(f"ALTER TABLE {self._q(table_name)} RENAME COLUMN {self._col(old_name)} TO {self._col(new_name)}")

    # ------------------------------------------------------------------- write

    def _prepare_timestamp_column(
        self,
        df: pd.DataFrame,
        designated_timestamp: str,
    ) -> pd.DataFrame:
        if designated_timestamp not in df.columns:
            raise ValueError(f"designated_timestamp column {designated_timestamp!r} not in DataFrame columns")

        df = df.copy()
        series = df[designated_timestamp]
        if pd.api.types.is_datetime64_any_dtype(series):
            if getattr(series.dtype, "tz", None) is not None:
                df[designated_timestamp] = series.dt.tz_localize(None)
            return df

        if series.dtype == object:

            def to_naive(value):
                if isinstance(value, pd.Timestamp):
                    return value.tz_localize(None) if value.tzinfo is not None else value
                if isinstance(value, datetime):
                    return value.replace(tzinfo=None)
                return value

            df[designated_timestamp] = series.map(to_naive)
        return df

    def _prepare_copy_value(self, value):
        value = self._normalize_scalar(value)
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, np.datetime64):
            return pd.Timestamp(value).to_pydatetime()
        if isinstance(value, tuple):
            return list(value)
        return value

    def _insert_large_dataframe_via_copy(
        self,
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str,
        conflict_columns: Sequence[str],
    ) -> int:
        if importlib.util.find_spec("psycopg") is None:
            raise RuntimeError("psycopg is required for COPY-based bulk inserts")

        stage_name = f"stg_{table_name}_{uuid4().hex}"
        columns = list(df.columns)
        quoted_columns = [self._col(column) for column in columns]
        quoted_conflict_columns = [self._col(column) for column in conflict_columns]
        target_quoted = self._q(table_name)
        stage_quoted = f'"{stage_name}"'
        copy_sql = f"COPY {stage_quoted} ({', '.join(quoted_columns)}) FROM STDIN"
        set_clauses = [
            f'{self._col(column)} = EXCLUDED.{self._col(column)}'
            for column in columns
            if column not in conflict_columns
        ]
        insert_sql = (
            f"INSERT INTO {target_quoted} ({', '.join(quoted_columns)}) "
            f"SELECT {', '.join(quoted_columns)} FROM {stage_quoted} "
            f"ON CONFLICT ({', '.join(quoted_conflict_columns)}) DO UPDATE SET {', '.join(set_clauses)}"
        )

        with self._engine.begin() as conn:
            conn.execute(text(f"CREATE TEMP TABLE {stage_quoted} AS TABLE {target_quoted} WITH NO DATA"))
            dbapi_conn = conn.connection
            with dbapi_conn.cursor() as cur:
                with cur.copy(copy_sql) as copy:
                    for row in df.itertuples(index=False, name=None):
                        copy.write_row([self._prepare_copy_value(value) for value in row])
            conn.execute(text(insert_sql))
        return len(df)

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

        _check_identifier(table_name)
        _check_identifier(designated_timestamp)
        self._ensure_environment()
        if not self._cached_table_exists(table_name):
            raise ValueError(f"Table {table_name} does not exist. Please create it first.")

        df = self._prepare_timestamp_column(df, designated_timestamp)
        for column in df.columns:
            _check_identifier(column)

        conflict_columns = [designated_timestamp]
        for column in symbol_columns or []:
            _check_identifier(column)
            if column != designated_timestamp:
                conflict_columns.append(column)
        conflict_columns = list(dict.fromkeys(conflict_columns))

        if len(df) >= 10000:
            return self._insert_large_dataframe_via_copy(table_name, df, designated_timestamp, conflict_columns)

        method = partial(_insert_on_conflict_update, indexes=conflict_columns)
        df.to_sql(
            table_name,
            self._engine,
            schema=self._schema,
            if_exists="append",
            index=False,
            method=method,
            chunksize=2048,
        )
        return len(df)

    # -------------------------------------------------------------------- read

    def read_data(
        self,
        table_name: str,
        query_fields="*",
        filter_fields=None,
        unique: bool = False,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        self._ensure_environment()
        fields = self._select_fields(query_fields)
        where, params = self._build_where(filter_fields)
        sql = f"SELECT {'DISTINCT ' if unique else ''}{fields} FROM {self._q(table_name)}"
        if where:
            sql += f" WHERE {where}"
        return self._read_sql(sql, params)

    def read_range_data(
        self,
        table_name: str,
        query_fields="*",
        start_time=None,
        end_time=None,
        time_column: str = None,
        filter_fields=None,
        sample_by: str = None,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        self._ensure_environment()
        ts_col = time_column or "timestamp"
        _check_identifier(ts_col)
        fields = self._select_fields(query_fields)

        conditions = []
        params = {}
        if start_time is not None:
            key = f"p{len(params)}"
            params[key] = self._normalize_scalar(start_time)
            conditions.append(f"{self._col(ts_col)} >= :{key}")
        if end_time is not None:
            key = f"p{len(params)}"
            params[key] = self._normalize_scalar(end_time)
            conditions.append(f"{self._col(ts_col)} <= :{key}")

        filter_where, params = self._build_where(filter_fields, params=params)
        if filter_where:
            conditions.append(filter_where)

        sql = f"SELECT {fields} FROM {self._q(table_name)}"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if sample_by:
            warnings.warn(
                "sample_by in read_range_data is not supported by TimescaleManager; use sample_by() instead",
                stacklevel=2,
            )
        return self._read_sql(sql, params)

    def _resolve_cross_table_columns(self, query_fields, table_name: str):
        if query_fields == "*":
            return "*"
        if isinstance(query_fields, dict):
            values = query_fields.get(table_name, [])
        elif isinstance(query_fields, list):
            values = query_fields
        else:
            values = []
        if values is None:
            values = []
        if isinstance(values, str):
            values = [values]
        return list(values)

    def _read_single_table(self, table_name: str, select_clause: str, filter_fields=None):
        self._ensure_environment()
        where, params = self._build_where(filter_fields)
        sql = f"SELECT {select_clause} FROM {self._q(table_name)}"
        if where:
            sql += f" WHERE {where}"
        return self._read_sql(sql, params)

    def read_data_across_tables(
        self,
        table_names: Sequence[str],
        join_columns: Sequence[str],
        query_fields: str | dict | list = "*",
        filter_fields=None,
        join_type: str = "inner",
        timezone: str | None = None,
    ) -> pd.DataFrame:
        if len(table_names) < 2:
            raise ValueError("At least 2 table names are required")
        if join_type not in ("inner", "asof"):
            raise ValueError(f"Unsupported join_type: {join_type}")

        t0, t1 = table_names[0], table_names[1]

        if join_type == "asof":

            def asof_select(table_name: str) -> str:
                requested = self._resolve_cross_table_columns(query_fields, table_name)
                if requested == "*":
                    return "*"
                need = [col for col in join_columns if col not in requested]
                columns = requested + need
                return self._select_fields(columns) if columns else "*"

            df0 = self._read_single_table(t0, asof_select(t0), (filter_fields or {}).get(t0))
            df1 = self._read_single_table(t1, asof_select(t1), (filter_fields or {}).get(t1))
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

        if query_fields == "*":
            select_clause = "t0.*, t1.*"
        elif isinstance(query_fields, dict):
            parts = []
            for table_name, columns in query_fields.items():
                alias = "t0" if table_name == t0 else ("t1" if table_name == t1 else table_name)
                if isinstance(columns, str):
                    parts.append(self._col_ref(columns, alias))
                elif columns:
                    parts.extend(self._col_ref(column, alias) for column in columns)
            select_clause = ", ".join(parts)
        else:
            select_clause = "t0.*, t1.*"

        join_condition = " AND ".join(f"t0.{self._col(col)} = t1.{self._col(col)}" for col in join_columns)
        sql = f"SELECT {select_clause} FROM {self._q(t0)} t0 INNER JOIN {self._q(t1)} t1 ON {join_condition}"

        conditions = []
        params = {}
        for table_name in table_names:
            alias = "t0" if table_name == t0 else ("t1" if table_name == t1 else table_name)
            where, params = self._build_where((filter_fields or {}).get(table_name), prefix=alias, params=params)
            if where:
                conditions.append(where)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return self._read_sql(sql, params)

    def read_max_in_group(
        self,
        table_name: str,
        target_column,
        group_column,
        extremum_column: str,
        filter_fields=None,
        timezone: str | None = None,
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
        timezone: str | None = None,
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
        self._ensure_environment()
        target_cols = [target_column] if isinstance(target_column, str) else list(target_column)
        group_cols = [group_column] if isinstance(group_column, str) else list(group_column)
        _check_identifier(extremum_column)
        all_cols = list(dict.fromkeys(group_cols + target_cols))
        col_list = ", ".join(self._col(col) for col in all_cols)

        where, params = self._build_where(filter_fields)
        where_clause = f" WHERE {where}" if where else ""
        group_by = ", ".join(self._col(col) for col in group_cols)
        order_by = f"{self._col(extremum_column)} {order}"

        sql = (
            f"WITH c AS ("
            f"  SELECT {col_list}, "
            f"    ROW_NUMBER() OVER (PARTITION BY {group_by} "
            f"      ORDER BY {order_by}) AS rn"
            f"  FROM {self._q(table_name)}{where_clause}"
            f") SELECT {col_list} FROM c WHERE rn = 1"
        )
        return self._read_sql(sql, params)

    def sample_by(
        self,
        table_name: str,
        interval: str = "1m",
        aggregations: dict[str, str] = None,
        timestamp_column: str = None,
        filter_fields=None,
        fill: str = None,
        align_to_calendar: bool = True,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        self._ensure_environment()
        ts_col = timestamp_column or "timestamp"
        _check_identifier(ts_col)
        where, params = self._build_where(filter_fields)
        where_clause = f" WHERE {where}" if where else ""

        if not aggregations:
            warnings.warn(
                "sample_by without aggregations is not supported; returning ungrouped rows",
                stacklevel=2,
            )
            return self._read_sql(f"SELECT * FROM {self._q(table_name)}{where_clause}", params)

        interval_key = f"p{len(params)}"
        params[interval_key] = normalize_timescale_interval(interval)
        bucket_expr = f"time_bucket(CAST(:{interval_key} AS INTERVAL), {self._col(ts_col)}) AS {self._col(ts_col)}"
        agg_exprs = []
        for col, func in aggregations.items():
            agg_func = func.strip().lower()
            if agg_func in {"first", "last"}:
                direction = "ASC" if agg_func == "first" else "DESC"
                agg_exprs.append(
                    f"(array_agg({self._col(col)} ORDER BY {self._col(ts_col)} {direction}))[1] AS {self._col(col)}"
                )
            else:
                agg_exprs.append(f"{translate_agg_func(func)}({self._col(col)}) AS {self._col(col)}")
        sql = f"SELECT {bucket_expr}, {', '.join(agg_exprs)} FROM {self._q(table_name)}{where_clause} GROUP BY 1"
        if fill:
            warnings.warn("sample_by(fill=...) is not yet supported; returning unfilled buckets", stacklevel=2)
        if not align_to_calendar:
            warnings.warn("sample_by(align_to_calendar=False) is not supported; ignoring", stacklevel=2)
        return self._read_sql(sql, params)

    def latest_on(
        self,
        table_name: str,
        partition_by,
        timestamp_column: str = None,
        filter_fields=None,
        query_fields="*",
        search_start=None,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        self._ensure_environment()
        ts_col = timestamp_column or "timestamp"
        _check_identifier(ts_col)
        partition_cols = [partition_by] if isinstance(partition_by, str) else list(partition_by)
        for column in partition_cols:
            _check_identifier(column)

        if query_fields == "*":
            requested_cols = self._table_columns(table_name)
        else:
            requested_cols = [query_fields] if isinstance(query_fields, str) else list(query_fields)

        selected_cols = list(dict.fromkeys(partition_cols + requested_cols + [ts_col]))
        select_clause = ", ".join(self._col(col) for col in selected_cols)

        def _query_one(batch_filter_fields):
            conditions = []
            params = {}
            if search_start is not None:
                key = f"p{len(params)}"
                params[key] = self._normalize_scalar(search_start)
                conditions.append(f"{self._col(ts_col)} >= :{key}")
            filter_where, params = self._build_where(batch_filter_fields, params=params)
            if filter_where:
                conditions.append(filter_where)
            where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

            distinct_on = ", ".join(self._col(col) for col in partition_cols)
            order_by = ", ".join(self._col(col) for col in partition_cols)
            order_by += f", {self._col(ts_col)} DESC"

            sql = (
                f"SELECT DISTINCT ON ({distinct_on}) {select_clause} "
                f"FROM {self._q(table_name)}{where_clause} "
                f"ORDER BY {order_by}"
            )
            return self._read_sql(sql, params)

        if not filter_fields:
            return _query_one(None)

        sequence_fields = {
            field: values
            for field, values in filter_fields.items()
            if isinstance(values, Container) and not isinstance(values, (str, bytes))
        }
        if not sequence_fields:
            return _query_one(filter_fields)

        max_values_per_query = 1024
        batch_frames = []
        field_names = list(sequence_fields.keys())
        field_chunks = {
            field: [list(values)[i : i + max_values_per_query] for i in range(0, len(values), max_values_per_query)]
            for field, values in sequence_fields.items()
        }
        chunk_count = max(len(chunks) for chunks in field_chunks.values())

        for idx in range(chunk_count):
            batch_filter_fields = {
                key: value for key, value in (filter_fields or {}).items() if key not in sequence_fields
            }
            for field in field_names:
                chunks = field_chunks[field]
                if idx < len(chunks):
                    batch_filter_fields[field] = chunks[idx]
            batch_frames.append(_query_one(batch_filter_fields))

        result = pd.concat(batch_frames, ignore_index=True)
        if not result.empty:
            result = result.drop_duplicates()
        return result
