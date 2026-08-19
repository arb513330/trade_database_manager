# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : iotmanager.py
# @Purpose : Manage Apache IoTDB (table model) operations via the apache-iotdb SDK

import re
import warnings
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


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe column name: {name!r}")


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
        self._query_timeout_ms = int(CONFIG.get("iotdb_query_timeout_ms", 0) or 0)
        self._latest_timeout_ms = int(
            CONFIG.get("iotdb_latest_timeout_ms", 0)
            or CONFIG.get("iotdb_query_timeout_ms", 0)
            or 180_000
        )
        # Canonical UTC session: timestamps are rendered/interpreted as UTC
        # instants. Callers pass `timezone=` per read to display in a specific
        # zone (e.g. a symbol's native exchange timezone from the metadata DB).
        self._time_zone = "UTC"

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
            time_zone=self._time_zone,
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
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
            self._connect()
            return fn()

    def _execute_sql(self, sql: str) -> None:
        self._with_reconnect(lambda: self._session.execute_non_query_statement(sql))

    def _execute_query(
        self,
        sql: str,
        timezone: str | None = None,
        timeout_in_ms: int | None = None,
    ) -> pd.DataFrame:
        if timeout_in_ms is None:
            timeout_in_ms = self._query_timeout_ms

        def run() -> pd.DataFrame:
            ds = self._session.execute_query_statement(sql, timeout_in_ms)
            try:
                return self._convert_tz(ds.todf(), timezone)
            finally:
                ds.close_operation_handle()
        return self._with_reconnect(run)

    def _convert_tz(self, df: pd.DataFrame, timezone: str | None = None) -> pd.DataFrame:
        """Convert returned timestamp columns to ``timezone`` (tz-aware output).

        IoTDB returns timestamps already tz-aware in the session timezone; the
        read API returns them tz-aware in ``timezone`` (default: session tz).
        Iterates positionally so duplicate column names (joins) are handled.
        """
        target = timezone or self._time_zone
        for i in range(df.shape[1]):
            ser = df.iloc[:, i]
            for v in ser:
                if isinstance(v, pd.Timestamp):
                    if v.tzinfo is not None:
                        df.isetitem(i, pd.to_datetime(ser).dt.tz_convert(target))
                    break
        return df

    # ---------------------------------------------------------------- helpers

    def _build_where(self, filter_fields, prefix: str | None = None) -> str:
        """Build an AND-joined WHERE condition string from a filter dict."""
        if not filter_fields:
            return ""

        def q(field):
            return f"{prefix}.{field}" if prefix else field

        conditions = []
        for field, value in filter_fields.items():
            if isinstance(value, str):
                conditions.append(f"{q(field)} = '{escape_string(value)}'")
            elif isinstance(value, Container) and not isinstance(value, (str, bytes)):
                formatted = ", ".join(
                    f"'{escape_string(v)}'" if isinstance(v, str) else str(v)
                    for v in value
                )
                conditions.append(f"{q(field)} IN ({formatted})")
            elif isinstance(value, (pd.Timestamp, np.datetime64, datetime)):
                conditions.append(f"{q(field)} = {format_time_literal(value, self._time_zone)}")
            elif isinstance(value, (bool, np.bool_)):
                conditions.append(f"{q(field)} = {'TRUE' if value else 'FALSE'}")
            else:
                conditions.append(f"{q(field)} = {value}")
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
            _check_identifier(name)
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
        for c in cols:
            _check_identifier(c)
        col_list = ", ".join(cols)
        rows = []
        for row in df.itertuples(index=False):
            vals = []
            for v in row:
                v1 = to_iotdb_value(v)
                if v1 is None:
                    vals.append("NULL")
                elif isinstance(v1, str):
                    vals.append(f"'{escape_string(v1)}'")
                elif isinstance(v1, datetime):
                    vals.append(format_time_literal(v1, self._time_zone))
                elif isinstance(v1, bool):
                    vals.append("TRUE" if v1 else "FALSE")
                else:
                    vals.append(str(v1))
            rows.append("(" + ", ".join(vals) + ")")
        return f"INSERT INTO {self._q(table_name)} ({col_list}) VALUES " + ", ".join(rows)

    @staticmethod
    def _build_tablets(
        table_name: str,
        df: pd.DataFrame,
        designated_timestamp: str,
        symbols: list[str],
    ) -> list[NumpyTablet]:
        """Chunk df by symbol and build one NumpyTablet per group (table model allows
        mixed tags in one tablet, but per-symbol chunks keep memory bounded)."""
        fields = [c for c in df.columns if c != designated_timestamp and c not in symbols]
        target = table_name
        tablets = []
        for _, grp in df.groupby(symbols) if symbols else [("__all__", df)]:
            timestamps = pd.to_datetime(grp[designated_timestamp]).values.astype('datetime64[ms]').astype("int64")
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
                    if ser.isna().any():
                        arr = ser.to_numpy(dtype="float64").copy()
                        for i in range(len(arr)):
                            if np.isnan(arr[i]):
                                bm.mark(i)
                                arr[i] = 0.0
                        values.append(arr.astype("int64"))
                    else:
                        values.append(ser.to_numpy(dtype="int64"))
                elif t == TSDataType.TIMESTAMP:
                    ser_t = pd.to_datetime(ser)
                    arr = ser_t.values.astype('datetime64[ms]').astype("int64")
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
            self._execute_query(f"DESCRIBE {self._q(table_name)}")
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
    @staticmethod
    def _check_tz_aware(df: pd.DataFrame, designated_timestamp: str) -> None:
        """Require the designated timestamp column to be timezone-aware.

        Naive timestamps are ambiguous about their zone; IoTDB stores an instant
        (epoch ms), so callers must supply tz-aware pd.Timestamp / datetime.
        """
        if designated_timestamp not in df.columns:
            raise ValueError(
                f"designated_timestamp column {designated_timestamp!r} not in DataFrame columns"
            )
        col = df[designated_timestamp]
        if col.dtype.kind == "M":  # datetime64, incl. tz-aware
            if getattr(col.dtype, "tz", None) is not None:
                return  # tz-aware datetime64[tz]
            raise ValueError(
                f"DataFrame {designated_timestamp!r} must be timezone-aware; "
                "use tz-aware pd.Timestamp / datetime (e.g. tz='Asia/Shanghai')"
            )
        if col.dtype == object:
            for v in col:
                if pd.isna(v):
                    continue
                if isinstance(v, pd.Timestamp) and v.tzinfo is not None:
                    continue
                if isinstance(v, datetime) and v.tzinfo is not None:
                    continue
                raise ValueError(
                    f"DataFrame {designated_timestamp!r} must contain only timezone-aware "
                    "pd.Timestamp / datetime values"
                )
            return
        raise ValueError(
            f"DataFrame {designated_timestamp!r} must be a datetime-like, timezone-aware column"
        )

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
        self._check_tz_aware(df, designated_timestamp)
        if use_ilp is None:
            use_ilp = len(df) > 1000
        if use_ilp:
            return self._insert_tablet(table_name, df, designated_timestamp, symbol_columns)
        return self._insert_sql(table_name, df, designated_timestamp)

    def _insert_sql(self, table_name: str, df: pd.DataFrame, designated_timestamp: str) -> int:
        self._execute_sql(self._build_insert_sql(table_name, df, designated_timestamp))
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
        timezone: str | None = None,
    ) -> pd.DataFrame:
        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        sql = f"SELECT {'DISTINCT ' if unique else ''}{fields} FROM {self._q(table_name)}"
        where = self._build_where(filter_fields)
        if where:
            sql += f" WHERE {where}"
        return self._execute_query(sql, timezone=timezone)

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
        ts_col = time_column or "timestamp"
        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        sql = f"SELECT {fields} FROM {self._q(table_name)}"
        conditions = []
        if start_time is not None:
            conditions.append(f"{ts_col} >= {format_time_literal(start_time, self._time_zone)}")
        if end_time is not None:
            conditions.append(f"{ts_col} <= {format_time_literal(end_time, self._time_zone)}")
        filter_where = self._build_where(filter_fields)
        if filter_where:
            conditions.append(filter_where)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if sample_by:
            # No aggregations are known here, so time-bucketing is not possible
            # on the table model; use sample_by() for explicit aggregations.
            warnings.warn(
                "sample_by in read_range_data is not supported on IoTDB; use sample_by() instead", stacklevel=2
            )
        return self._execute_query(sql, timezone=timezone)

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

            df0 = self._execute_query(f"SELECT {asof_select(t0)} FROM {self._q(t0)}{table_where(t0)}", timezone=timezone)
            df1 = self._execute_query(f"SELECT {asof_select(t1)} FROM {self._q(t1)}{table_where(t1)}", timezone=timezone)
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
            alias = "t0" if tn == t0 else ("t1" if tn == t1 else tn)
            w = self._build_where((filter_fields or {}).get(tn), prefix=alias)
            if w:
                conditions.append(w)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return self._execute_query(sql, timezone=timezone)

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
            table_name, target_column, group_column, extremum_column, "DESC", filter_fields, timezone
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
            table_name, target_column, group_column, extremum_column, "ASC", filter_fields, timezone
        )

    def _read_extremum_in_group(
        self,
        table_name: str,
        target_column,
        group_column,
        extremum_column: str,
        order: str,
        filter_fields=None,
        timezone: str | None = None,
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
        return self._execute_query(sql, timezone=timezone)

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
        ts_col = timestamp_column or "timestamp"
        if not aggregations:
            # Can't GROUP BY without knowing which columns to aggregate.
            warnings.warn(
                "sample_by without aggregations is not supported on IoTDB; "
                "returning ungrouped rows",
                stacklevel=2
            )
            sql = f"SELECT * FROM {self._q(table_name)}"
            where = self._build_where(filter_fields)
            if where:
                sql += f" WHERE {where}"
            return self._execute_query(sql, timezone=timezone)

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
            warnings.warn("sample_by(fill=...) is not yet supported; returning unfilled buckets", stacklevel=2)
        if not align_to_calendar:
            warnings.warn("sample_by(align_to_calendar=False) has no IoTDB equivalent; ignoring", stacklevel=2)
        return self._execute_query(sql, timezone=timezone)

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
        ts_col = timestamp_column or "timestamp"
        partition_cols = partition_by if isinstance(partition_by, list) else [partition_by]
        partition_sql = ", ".join(partition_cols)
        partition_set = set(partition_cols)

        if query_fields == "*":
            desc = self._execute_query(f"DESCRIBE {self._q(table_name)}")
            column_name_col = next(
                c for c in desc.columns if c.lower() == "columnname"
            )
            requested_cols = desc[column_name_col].astype(str).tolist()
        else:
            requested_cols = [query_fields] if isinstance(query_fields, str) else list(query_fields)

        # Native latest-per-partition. last_by(field, timestamp) returns the
        # value from the row carrying the newest timestamp, unlike last(field),
        # which returns the newest non-null value.
        select_parts = []
        seen = set()
        for col in requested_cols:
            if col in seen:
                continue
            seen.add(col)
            if col == ts_col:
                select_parts.append(f"last({ts_col}) AS {ts_col}")
            elif col in partition_set:
                select_parts.append(col)
            else:
                select_parts.append(f"last_by({col}, {ts_col}) AS {col}")
        select_clause = ", ".join(select_parts)

        conditions = []
        if search_start is not None:
            conditions.append(f"{ts_col} >= {format_time_literal(search_start, self._time_zone)}")
        filter_where = self._build_where(filter_fields)
        if filter_where:
            conditions.append(filter_where)
        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        sql = (
            f"SELECT {select_clause} FROM {self._q(table_name)}"
            f"{where_clause} GROUP BY {partition_sql}"
        )
        return self._execute_query(
            sql,
            timezone=timezone,
            timeout_in_ms=self._latest_timeout_ms,
        )
