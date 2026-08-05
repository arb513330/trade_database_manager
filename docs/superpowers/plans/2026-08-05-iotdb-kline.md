# IoTDB KLine Storage — IoTManager + KLineManager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `IoTManager` (core) and `KLineManager` (manager) so KLine OHLCV data can be stored in Apache IoTDB (table model) as a drop-in replacement for QuestDB.

**Architecture:** `IoTManager` wraps a persistent `TableSession` (lazy, reconnect-on-failure) and mirrors every public method of `QuestManager`, translating QuestDB semantics to IoTDB table-model SQL. All tables live in one database (`tradedata`) as `tradedata.{inst_type}_{interval}`. `KLineManager` is a near-clone of `kline_questdb.py` with per-instrument-type column schemas.

**Tech Stack:** Python 3, apache-iotdb SDK v2.0.10, pandas, numpy. Server: `192.168.1.29:6667`.

**Design spec:** `docs/superpowers/specs/2026-08-05-iotdb-kline-design.md` (commit `6b9d0b2`).

## Global Constraints

- **NO real IoTDB connection during this plan's execution.** The user fills in real credentials after the first round; the round-trip verification script (`debug_test/test_kline_iotdb.py`) is written but NOT run in this plan.
- Per-task verification uses `python -m py_compile` + offline `python - <<'PY'` assertions only (imports and pure functions that need no server). The project has **no pytest suite** (see CLAUDE.md) — verification is via ad-hoc checks.
- Connection config comes from `~/.tradedbmgr/config.yaml` via `from ...config import CONFIG`. Add `iotdb_host`, `iotdb_port`, `iotdb_user`, `iotdb_password`, `iotdb_database` — with **placeholder** `iotdb_user`/`iotdb_password` values.
- All SQL-qualified table references use the configured database: `{database}.{table}` (e.g. `tradedata.FUT_1m`). The TIME column is named `timestamp`.
- Existing `kline_questdb.py`, `QuestManager`, and the `manager/kline/__init__.py` / `manager/__init__.py` exports stay **unchanged**.
- IoTDB syntax items that cannot be verified without a server are flagged `[VERIFY-ON-SERVER]` — the test phase resolves them; do not silently change behavior.
- Repo root for all commands: `D:\Documents\TradeResearch\trade_database_manager`. Package dir: `trade_database_manager/`.

---

### Task 1: Add `iotdb_*` placeholder keys to auth config

**Files:**
- Modify: `~/.tradedbmgr/config.yaml` (outside the repo — no git commit)

**Interfaces:**
- Consumes: nothing
- Produces: config keys read by `IoTManager.__init__` in Task 3: `iotdb_host`, `iotdb_port`, `iotdb_user`, `iotdb_password`, `iotdb_database`

- [ ] **Step 1: Append the `iotdb_*` block if not already present**

Run (from repo root) — appends to the top-level YAML mapping without printing any existing credential values:

```bash
python - <<'PY'
import os
p = os.path.expanduser("~/.tradedbmgr/config.yaml")
with open(p, encoding="utf-8") as f:
    content = f.read()

if "iotdb_host" not in content:
    block = (
        '\n# Apache IoTDB (table model)\n'
        'iotdb_host: "192.168.1.29"\n'
        'iotdb_port: 6667\n'
        'iotdb_user: "<your_iotdb_user>"\n'
        'iotdb_password: "<your_iotdb_password>"\n'
        'iotdb_database: "tradedata"\n'
    )
    with open(p, "a", encoding="utf-8") as f:
        f.write(block)
    print("iotdb keys appended")
else:
    print("iotdb keys already present")
PY
```

- [ ] **Step 2: Verify the keys load and placeholders are in place**

```bash
python - <<'PY'
import os, yaml
cfg = yaml.safe_load(open(os.path.expanduser("~/.tradedbmgr/config.yaml")))
assert cfg["iotdb_host"] == "192.168.1.29"
assert cfg["iotdb_port"] == 6667
assert cfg["iotdb_database"] == "tradedata"
assert cfg["iotdb_user"].startswith("<") and cfg["iotdb_password"].startswith("<")
print("OK: iotdb config present, placeholders intact,", len(cfg), "top-level keys")
PY
```

Expected: prints `OK: iotdb config present, placeholders intact, N top-level keys`. No credentials printed.

---

### Task 2: `core/iotdb/utils.py` — type inference & SQL helpers

**Files:**
- Create: `trade_database_manager/core/iotdb/utils.py`

**Interfaces:**
- Consumes: nothing (std lib + numpy + pandas only)
- Produces (used by Task 3):
  - `infer_iotdb_type(py_type: type) -> str` — maps Python type to `STRING`/`INT64`/`DOUBLE`/`BOOLEAN`/`TIMESTAMP`/`DATE`
  - `to_iotdb_value(value) -> Any` — normalizes numpy/pandas scalars; NaN/NaT → `None`
  - `translate_agg_func(func: str) -> str` — QuestDB agg name → IoTDB (`first`→`FIRST_VALUE`, `last`→`LAST_VALUE`, `min`→`MIN_VALUE`, `max`→`MAX_VALUE`, `sum`→`SUM`, `avg`/`mean`→`AVG`, `count`→`COUNT`)
  - `format_time_literal(ts) -> str` — datetime-like → bare IoTDB timestamp literal `YYYY-MM-DD HH:MM:SS` (unquoted)
  - `escape_string(value: str) -> str` — doubles single quotes

- [ ] **Step 1: Write the file**

```python
# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : utils.py
# @Purpose : IoTDB type inference and value conversion utilities

from datetime import date, datetime

import numpy as np
import pandas as pd

# QuestDB-style aggregate names -> IoTDB table-model aggregate functions
AGG_FUNC_MAP = {
    "first": "FIRST_VALUE",
    "last": "LAST_VALUE",
    "min": "MIN_VALUE",
    "max": "MAX_VALUE",
    "sum": "SUM",
    "avg": "AVG",
    "mean": "AVG",
    "count": "COUNT",
}


def infer_iotdb_type(py_type) -> str:
    """Infer an IoTDB table-model column type from a Python type."""
    if py_type is None:
        raise ValueError(f"Unsupported type {py_type}")
    if issubclass(py_type, (bool, np.bool_)):
        return "BOOLEAN"
    if issubclass(py_type, (int, np.integer)):
        return "INT64"
    if issubclass(py_type, (float, np.floating)):
        return "DOUBLE"
    if issubclass(py_type, str):
        return "STRING"
    if issubclass(py_type, (np.datetime64, pd.Timestamp, datetime)):
        return "TIMESTAMP"
    if issubclass(py_type, date):
        return "DATE"
    raise ValueError(f"Unsupported type {py_type}")


def to_iotdb_value(value):
    """Normalize a Python/numpy/pandas scalar for IoTDB insertion; NaN -> None."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).to_pydatetime()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def translate_agg_func(func: str) -> str:
    """Map a QuestDB-style aggregate name to the IoTDB table-model equivalent."""
    upper = func.strip().upper()
    if upper in set(AGG_FUNC_MAP.values()):
        return upper
    mapped = AGG_FUNC_MAP.get(func.strip().lower())
    if mapped is None:
        raise ValueError(f"Unsupported aggregate function: {func}")
    return mapped


def format_time_literal(ts) -> str:
    """Format a datetime-like as an IoTDB bare timestamp literal (unquoted)."""
    return pd.Timestamp(ts).to_pydatetime().strftime("%Y-%m-%d %H:%M:%S")


def escape_string(value) -> str:
    """Escape a string literal for embedding in IoTDB SQL."""
    return str(value).replace("'", "''")
```

- [ ] **Step 2: Verify offline — compile + assertion checks**

```bash
python -m py_compile trade_database_manager/core/iotdb/utils.py
python - <<'PY'
from datetime import datetime
import numpy as np, pandas as pd
from trade_database_manager.core.iotdb.utils import (
    infer_iotdb_type, to_iotdb_value, translate_agg_func,
    format_time_literal, escape_string,
)
assert infer_iotdb_type(bool) == "BOOLEAN"
assert infer_iotdb_type(int) == "INT64"
assert infer_iotdb_type(np.int64) == "INT64"
assert infer_iotdb_type(float) == "DOUBLE"
assert infer_iotdb_type(str) == "STRING"
assert infer_iotdb_type(pd.Timestamp) == "TIMESTAMP"
assert to_iotdb_value(np.int64(5)) == 5
assert to_iotdb_value(np.float64(2.5)) == 2.5
assert to_iotdb_value(float("nan")) is None
assert to_iotdb_value(pd.NaT) is None
assert translate_agg_func("first") == "FIRST_VALUE"
assert translate_agg_func("avg") == "AVG"
assert translate_agg_func("SUM") == "SUM"
assert translate_agg_func("max") == "MAX_VALUE"
try:
    translate_agg_func("median")
    raise AssertionError("should have raised")
except ValueError:
    pass
assert format_time_literal(pd.Timestamp("2026-08-01 09:30:15")) == "2026-08-01 09:30:15"
assert escape_string("O'Reilly") == "O''Reilly"
print("OK: utils.py offline checks passed")
PY
```

Expected: `OK: utils.py offline checks passed`.

- [ ] **Step 3: Commit**

```bash
git add trade_database_manager/core/iotdb/utils.py
git commit -m "feat(core): add IoTDB type/value utilities

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: `core/iotdb/iotmanager.py` — `IoTManager` (full parity)

**Files:**
- Create: `trade_database_manager/core/iotdb/iotmanager.py`

**Interfaces:**
- Consumes: `utils` from Task 2; `CONFIG` from `trade_database_manager.config`; SDK classes:
  - `from iotdb.table_session import TableSession, TableSessionConfig`
  - `from iotdb.utils.IoTDBConstants import TSDataType`
  - `from iotdb.utils.Tablet import ColumnType`
  - `from iotdb.utils.BitMap import BitMap`
  - `from iotdb.utils.NumpyTablet import NumpyTablet`
  - `from iotdb.utils.exception import IoTDBConnectionException`
- Produces (used by Task 4): `IoTManager` with methods `table_exists`, `create_table`, `insert_column`, `delete_column`, `rename_column`, `insert`, `read_data`, `read_range_data`, `read_data_across_tables`, `read_max_in_group`, `read_min_in_group`, `sample_by`, `latest_on(search_start=...)`.

- [ ] **Step 1: Write the file**

```python
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
                return ds.todf()
            finally:
                ds.close_operation_handle()
        return self._with_reconnect(run)

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
                    arr = ser.to_numpy(dtype="float64")
                    for i in range(len(arr)):
                        if np.isnan(arr[i]):
                            bm.mark(i)
                            arr[i] = 0.0
                    values.append(arr)
                elif t == TSDataType.INT64:
                    arr = ser.to_numpy(dtype="float64")
                    for i in range(len(arr)):
                        if np.isnan(arr[i]):
                            bm.mark(i)
                            arr[i] = 0.0
                    values.append(arr.astype("int64"))
                elif t == TSDataType.TIMESTAMP:
                    values.append(pd.to_datetime(ser).values.astype("int64") // 10**6)
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

    def rename_column(self, table_name: str, old_name: str, new_name: str):  # [VERIFY-ON-SERVER]
        self._execute_sql(
            f"ALTER TABLE {self._q(table_name)} RENAME COLUMN {old_name} TO {new_name}"
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
            self._session.insert(tablet)
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
            sql += f" GROUP BY TIME({sample_by})"  # [VERIFY-ON-SERVER]
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

        def table_where(tn: str) -> str:
            w = self._build_where((filter_fields or {}).get(tn))
            return f" WHERE {w}" if w else ""

        if join_type == "asof":
            # IoTDB table model has no ASOF JOIN; faithful fallback via merge_asof.
            df0 = self._execute_query(f"SELECT * FROM {self._q(t0)}{table_where(t0)}")
            df1 = self._execute_query(f"SELECT * FROM {self._q(t1)}{table_where(t1)}")
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

        join_cond = " AND ".join(f"{t0}.{c} = {t1}.{c}" for c in join_columns)
        sql = (
            f"SELECT {select_clause} FROM {self._q(t0)} "
            f"INNER JOIN {self._q(t1)} ON {join_cond}"  # [VERIFY-ON-SERVER]
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
        sql = (
            f"SELECT {col_list} FROM ("
            f"  SELECT {col_list}, "
            f"    ROW_NUMBER() OVER (PARTITION BY {', '.join(gcols)} "
            f"      ORDER BY {extremum_column} {order}) AS rn"
            f"  FROM {self._q(table_name)}{where_clause}"
            f") WHERE rn = 1"  # [VERIFY-ON-SERVER]
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
        if aggregations:
            agg_exprs = [
                f"{translate_agg_func(func)}({col}) AS {col}"
                for col, func in aggregations.items()
            ]
            select_clause = f"{ts_col}, " + ", ".join(agg_exprs)
        else:
            select_clause = "*"
        sql = f"SELECT {select_clause} FROM {self._q(table_name)}"
        where = self._build_where(filter_fields)
        if where:
            sql += f" WHERE {where}"
        sql += f" GROUP BY TIME({interval})"  # [VERIFY-ON-SERVER]
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
        fields = "*" if query_fields == "*" else ", ".join(query_fields)
        partition = ", ".join(partition_by) if isinstance(partition_by, list) else partition_by
        conditions = []
        if search_start is not None:
            conditions.append(f"{ts_col} >= {format_time_literal(search_start)}")
        filter_where = self._build_where(filter_fields)
        if filter_where:
            conditions.append(filter_where)
        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (
            f"SELECT {fields} FROM ("
            f"  SELECT {fields}, ROW_NUMBER() OVER (PARTITION BY {partition} "
            f"    ORDER BY {ts_col} DESC) AS rn"
            f"  FROM {self._q(table_name)}{where_clause}"
            f") WHERE rn = 1"  # [VERIFY-ON-SERVER]
        )
        df = self._execute_query(sql)
        if "rn" in df.columns:
            df = df.drop(columns=["rn"])
        return df
```

- [ ] **Step 2: Verify offline — compile + pure-function/SQL-builder checks (no connection)**

```bash
python -m py_compile trade_database_manager/core/iotdb/iotmanager.py
python - <<'PY'
import pandas as pd
from trade_database_manager.core.iotdb.iotmanager import IoTManager

m = IoTManager()  # lazy: must NOT connect
assert m._session is None
assert m._q("FUT_1m") == "tradedata.FUT_1m"

# _build_where
assert m._build_where({}) == ""
assert m._build_where({"full_symbol": "rb2401.SHFE"}) == "full_symbol = 'rb2401.SHFE'"
assert m._build_where({"full_symbol": ["a", "b"]}) == "full_symbol IN ('a', 'b')"
assert m._build_where({"open": 1.5}) == "open = 1.5"
assert m._build_where({"full_symbol": "O'Reilly"}) == "full_symbol = 'O''Reilly'"

# _build_create_sql
tag_set = {"full_symbol"}
sql = m._build_create_sql(
    "FUT_1m",
    [("timestamp", pd.Timestamp), ("full_symbol", str),
     ("open", float), ("open_interest", float)],
    "timestamp", tag_set,
)
assert "CREATE TABLE IF NOT EXISTS tradedata.FUT_1m" in sql
assert "timestamp TIMESTAMP TIME" in sql
assert "full_symbol STRING TAG" in sql
assert "open DOUBLE FIELD" in sql
assert "open_interest DOUBLE FIELD" in sql
print(sql)

# _build_insert_sql
df = pd.DataFrame({
    "timestamp": pd.to_datetime(["2026-08-01 09:30:00", "2026-08-01 09:31:00"]),
    "full_symbol": ["rb2401.SHFE"] * 2,
    "open": [1.0, 2.0],
})
ins = m._build_insert_sql("FUT_1m", df, "timestamp")
assert ins.startswith("INSERT INTO tradedata.FUT_1m (timestamp, full_symbol, open) VALUES ")
assert "(2026-08-01 09:30:00, 'rb2401.SHFE', 1.0)" in ins
assert "(2026-08-01 09:31:00, 'rb2401.SHFE', 2.0)" in ins
print(ins)

# _build_tablets
df2 = pd.DataFrame({
    "timestamp": pd.to_datetime(["2026-08-01 09:30:00", "2026-08-01 09:31:00"]),
    "full_symbol": ["rb2401.SHFE", "rb2402.SHFE"],
    "open": [1.0, float("nan")],
    "high": [2.0, 2.5],
})
tablets = m._build_tablets("FUT_1m", df2, "timestamp", ["full_symbol"])
assert len(tablets) == 2  # one tablet per symbol
for t in tablets:
    assert t.get_insert_target_name() == "tradedata.FUT_1m"
    assert t.get_row_number() == 1
print("OK: iotmanager.py offline checks passed")
PY
```

Expected: prints the generated CREATE/INSERT SQL followed by `OK: iotmanager.py offline checks passed`. `m._session is None` must hold (proves no connection attempt).

- [ ] **Step 3: Commit**

```bash
git add trade_database_manager/core/iotdb/iotmanager.py
git commit -m "feat(core): add IoTManager for Apache IoTDB table model

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `manager/kline/kline_iotdb.py` — `KLineManager`

**Files:**
- Create: `trade_database_manager/manager/kline/kline_iotdb.py`

**Interfaces:**
- Consumes: `IoTManager` from Task 3; `Interval` from `trade_database_manager.manager.typedefs`
- Produces (used by Task 5): `KLineManager` with `create_table(inst_type, interval, additional_fields=())`, `upsert(inst_type, interval, df)`, `read_range(...)`, `read_newest(..., search_start=None)`; module constants `KLINE_COMMON_COLUMNS`, `KLINE_TYPE_EXTRA_COLUMNS`.

- [ ] **Step 1: Write the file**

```python
# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : kline_iotdb.py
# @Purpose : K-line data management on Apache IoTDB

import pandas as pd

from ..typedefs import Interval
from ...core.iotdb.iotmanager import IoTManager

# Common to all instrument types.
KLINE_COMMON_COLUMNS = [
    ("timestamp", pd.Timestamp),
    ("full_symbol", str),
    ("open", float),
    ("high", float),
    ("low", float),
    ("close", float),
    ("volume", float),
    ("money", float),
    ("vwap", float),
]

# Extra columns per instrument type.
KLINE_TYPE_EXTRA_COLUMNS = {
    "FUT": [("open_interest", float), ("settlement", float)],
    "OPT": [("open_interest", float), ("settlement", float)],
}


class KLineManager:
    """Manage OHLCV kline data in Apache IoTDB, one table per inst_type + interval.

    Table format: ``{inst_type}_{interval}`` inside the configured IoTDB database,
    e.g. ``tradedata.FUT_1m``, ``tradedata.STK_1d``.

    :ivar IoTManager qm: The underlying IoTDB connection manager.
    """

    def __init__(self):
        self.qm = IoTManager()

    @staticmethod
    def _infer_partition(interval: Interval) -> str:
        return "MONTH" if interval.value < Interval.DAY.value else "YEAR"

    @staticmethod
    def _table_name(inst_type: str, interval: Interval) -> str:
        return f"{inst_type}_{interval.name}"

    @staticmethod
    def _base_columns(inst_type: str) -> list[tuple[str, type]]:
        return KLINE_COMMON_COLUMNS + KLINE_TYPE_EXTRA_COLUMNS.get(inst_type, [])

    def create_table(
        self,
        inst_type: str,
        interval: Interval,
        additional_fields: list[tuple[str, type]] = (),
    ):
        columns = self._base_columns(inst_type) + list(additional_fields)
        self.qm.create_table(
            table_name=self._table_name(inst_type, interval),
            columns=columns,
            designated_timestamp="timestamp",
            partition_by=self._infer_partition(interval),
            wal=True,
            dedup_keys=["timestamp", "full_symbol"],
            symbol_columns=["full_symbol"],
            indexed_columns=["full_symbol"],
        )

    def upsert(self, inst_type: str, interval: Interval, df: pd.DataFrame):
        assert isinstance(df.index, pd.MultiIndex) and set(df.index.names) == {
            "timestamp",
            "full_symbol",
        }, "DataFrame index must be a MultiIndex with timestamp and full_symbol"

        required = {c for c, _ in self._base_columns(inst_type)} - {"timestamp", "full_symbol"}
        assert set(df.columns) >= required, (
            f"DataFrame must contain all required columns for {inst_type}: {sorted(required)}"
        )

        table = self._table_name(inst_type, interval)
        if not self.qm.table_exists(table):
            self.create_table(inst_type, interval)

        df_flat = df.reset_index()
        self.qm.insert(
            table,
            df_flat,
            designated_timestamp="timestamp",
            symbol_columns=["full_symbol"],
        )

    def read_range(
        self,
        inst_type: str,
        interval: Interval,
        symbols: list[str] = None,
        start_time=None,
        end_time=None,
        columns: list[str] = None,
    ) -> pd.DataFrame:
        table = self._table_name(inst_type, interval)
        filter_fields = {"full_symbol": symbols} if symbols else None
        if columns is not None:
            seen = {"timestamp", "full_symbol"}
            query_fields = ["timestamp", "full_symbol"] + [c for c in columns if c not in seen]
        else:
            query_fields = "*"
        result = self.qm.read_range_data(
            table,
            query_fields=query_fields,
            start_time=start_time,
            end_time=end_time,
            time_column="timestamp",
            filter_fields=filter_fields,
        )
        if not result.empty and "timestamp" in result.columns and "full_symbol" in result.columns:
            result = result.set_index(["timestamp", "full_symbol"])
        return result

    def read_newest(
        self,
        inst_type: str,
        interval: Interval,
        symbols: list[str] = None,
        columns: list[str] = None,
        search_start=None,
    ) -> pd.DataFrame:
        table = self._table_name(inst_type, interval)
        filter_fields = {"full_symbol": symbols} if symbols else None
        if columns is not None:
            seen = {"timestamp", "full_symbol"}
            query_fields = ["timestamp", "full_symbol"] + [c for c in columns if c not in seen]
        else:
            query_fields = "*"
        result = self.qm.latest_on(
            table,
            query_fields=query_fields,
            partition_by="full_symbol",
            timestamp_column="timestamp",
            filter_fields=filter_fields,
            search_start=search_start,
        )
        if "timestamp" in result.columns and "full_symbol" in result.columns:
            result = result.set_index("full_symbol").rename(columns={"timestamp": "latest_timestamp"})
        return result
```

- [ ] **Step 2: Verify offline — compile + no-connection construction + schema composition**

```bash
python -m py_compile trade_database_manager/manager/kline/kline_iotdb.py
python - <<'PY'
import pandas as pd
from trade_database_manager.manager.kline.kline_iotdb import (
    KLineManager, KLINE_COMMON_COLUMNS, KLINE_TYPE_EXTRA_COLUMNS,
)
from trade_database_manager.manager.typedefs import Interval

km = KLineManager()  # lazy IoTManager: must NOT connect
assert km.qm._session is None
assert km._table_name("FUT", Interval.MIN01) == "FUT_MIN01"
assert km._table_name("STK", Interval.DAY) == "STK_DAY"
assert km._infer_partition(Interval.MIN01) == "MONTH"
assert km._infer_partition(Interval.DAY) == "YEAR"

fut_cols = dict(KLineManager._base_columns("FUT"))
stk_cols = dict(KLineManager._base_columns("STK"))
assert "open_interest" in fut_cols and "settlement" in fut_cols
assert "open_interest" not in stk_cols and "settlement" not in stk_cols
assert set(KLINE_COMMON_COLUMNS).issubset(set(fut_cols.items()))
print("FUT columns:", list(fut_cols))
print("STK columns:", list(stk_cols))
print("OK: kline_iotdb.py offline checks passed")
PY
```

Expected: prints both column lists and `OK: kline_iotdb.py offline checks passed`.

- [ ] **Step 3: Commit**

```bash
git add trade_database_manager/manager/kline/kline_iotdb.py
git commit -m "feat(manager): add KLineManager for Apache IoTDB

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: `debug_test/test_kline_iotdb.py` — round-trip verification script

**Files:**
- Create: `debug_test/test_kline_iotdb.py`

**Interfaces:**
- Consumes: `KLineManager` from Task 4; `Interval` from `trade_database_manager.manager.typedefs`
- Produces: the integration test script — **written now, run only after the user supplies real credentials** (see Task 6 handoff).

- [ ] **Step 1: Write the file**

```python
# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : test_kline_iotdb.py
# @Purpose : Round-trip verification for IoTDB KLine storage.
#
# NOTE: requires real credentials in ~/.tradedbmgr/config.yaml (iotdb_user /
# iotdb_password). Run from the repo root:
#   python debug_test/test_kline_iotdb.py

import pandas as pd

from trade_database_manager.manager.kline.kline_iotdb import KLineManager
from trade_database_manager.manager.typedefs import Interval


def main():
    km = KLineManager()

    # 1) Per-type table creation: STK (no open_interest), FUT (open_interest + settlement)
    km.create_table("STK", Interval.DAY)
    km.create_table("FUT", Interval.MIN01)

    # 2) Upsert STK bars (2 symbols x 3 days)
    idx = pd.MultiIndex.from_product(
        [pd.date_range("2026-08-01", periods=3, freq="D"), ["000001.SSE", "600519.SSE"]],
        names=["timestamp", "full_symbol"],
    )
    n = len(idx)
    stk = pd.DataFrame(
        {
            "open": [1.0] * n, "high": [2.0] * n, "low": [0.5] * n, "close": [1.5] * n,
            "volume": [100.0] * n, "money": [10000.0] * n, "vwap": [1.2] * n,
        },
        index=idx,
    )
    km.upsert("STK", Interval.DAY, stk)

    # 3) Upsert FUT bars (1 symbol x 3 minutes), with type-extra columns
    idxf = pd.MultiIndex.from_product(
        [pd.date_range("2026-08-01 09:00", periods=3, freq="1min"), ["rb2401.SHFE"]],
        names=["timestamp", "full_symbol"],
    )
    nf = len(idxf)
    fut = pd.DataFrame(
        {
            "open": [1.0] * nf, "high": [2.0] * nf, "low": [0.5] * nf, "close": [1.5] * nf,
            "volume": [10.0] * nf, "money": [1000.0] * nf, "vwap": [1.2] * nf,
            "open_interest": [5.0] * nf, "settlement": [1.4] * nf,
        },
        index=idxf,
    )
    km.upsert("FUT", Interval.MIN01, fut)

    # 4) Dedup: re-upsert identical rows -> rows are overwritten, not duplicated
    km.upsert("STK", Interval.DAY, stk)
    out = km.read_range("STK", Interval.DAY)
    assert len(out) == 6, f"expected 6 rows after re-upsert, got {len(out)}"

    # 5) read_range with type-extra columns present
    r = km.read_range("FUT", Interval.MIN01, symbols=["rb2401.SHFE"])
    assert len(r) == 3, f"expected 3 FUT rows, got {len(r)}"
    assert {"open", "settlement"}.issubset(r.columns), f"missing type columns: {list(r.columns)}"

    # 6) read_newest (all rows) and read_newest with search_start floor
    newest = km.read_newest("STK", Interval.DAY)
    assert sorted(newest.index) == ["000001.SSE", "600519.SSE"], f"bad newest: {newest.index.tolist()}"
    assert newest["latest_timestamp"].iloc[0] == pd.Timestamp("2026-08-03")

    newest2 = km.read_newest("STK", Interval.DAY, search_start=pd.Timestamp("2026-08-02"))
    assert sorted(newest2.index) == ["000001.SSE", "600519.SSE"]
    newest3 = km.read_newest("STK", Interval.DAY, search_start=pd.Timestamp("2026-08-05"))
    assert newest3.empty, "symbols with no rows after search_start must be absent"

    # 7) latest_on / group-extrema parity paths
    lat = km.qm.latest_on("STK_DAY", partition_by="full_symbol", timestamp_column="timestamp")
    assert len(lat) == 2
    grp = km.qm.read_max_in_group("STK_DAY", target_column="close", group_column="full_symbol",
                                  extremum_column="close")
    assert len(grp) == 2

    # 8) sample_by time bucketing
    s = km.qm.sample_by(
        "FUT_MIN01", interval="5m",
        aggregations={"close": "last", "volume": "sum"},
        filter_fields={"full_symbol": "rb2401.SHFE"},
    )
    assert not s.empty, "sample_by returned nothing"

    print("ALL ROUND-TRIP CHECKS PASSED")
    print("STK newest:")
    print(newest)
    print("FUT sample_by:")
    print(s)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it compiles (do NOT run)**

```bash
python -m py_compile debug_test/test_kline_iotdb.py
```

Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add debug_test/test_kline_iotdb.py
git commit -m "test: add IoTDB kline round-trip verification script

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Update CLAUDE.md architecture docs

**Files:**
- Modify: `CLAUDE.md` (repo root)

**Interfaces:**
- Consumes: nothing
- Produces: documentation of the new IoTDB modules

- [ ] **Step 1: Add IoTDB entries under `core/` and `manager/`**

Insert under the `core/` bullet list, after the `core/questdb/questmanager.py` bullet:

```markdown
   - `core/iotdb/iotmanager.py` — `IoTManager`: Apache IoTDB (table model) connector via the
     apache-iotdb SDK. Full-parity counterpart to `QuestManager`; one persistent `TableSession`,
     database-qualified tables (e.g. `tradedata.FUT_1m`), `NumpyTablet` bulk inserts.
```

Insert under the `manager/` bullet list, after the `manager/kline/` context (add a new bullet):

```markdown
   - `manager/kline/kline_iotdb.py` — `KLineManager`: K-line management on IoTDB, counterpart to
     `kline_questdb.py` with per-instrument-type columns (`FUT`/`OPT` add `open_interest` and
     `settlement`).
```

- [ ] **Step 2: Verify the edits are in place**

```bash
grep -n "core/iotdb" CLAUDE.md && grep -n "kline_iotdb" CLAUDE.md
```

Expected: two matching lines.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document IoTDB modules in CLAUDE.md

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Handoff — request real credentials, defer testing

No code changes. The first round is complete at this point.

- [ ] **Step 1: Tell the user the first round is done and ask them to fill in credentials**

Message to send:

> First implementation round is complete and committed:
> - `core/iotdb/utils.py` — type/value/SQL helpers
> - `core/iotdb/iotmanager.py` — `IoTManager` (full QuestManager parity)
> - `manager/kline/kline_iotdb.py` — `KLineManager` (per-type columns)
> - `debug_test/test_kline_iotdb.py` — round-trip verification script (compiled, **not run**)
> - `CLAUDE.md` — updated
> - `~/.tradedbmgr/config.yaml` — `iotdb_*` keys added with placeholder user/password
>
> No real IoTDB connection was made. Please replace `<your_iotdb_user>` / `<your_iotdb_password>`
> in `~/.tradedbmgr/config.yaml` with real credentials, then tell me to continue with testing.

- [ ] **Step 2: STOP.** Wait for the user to fill in credentials and say to continue. Do **not** run `debug_test/test_kline_iotdb.py` before then.

---

## Self-Review Notes

- **Spec coverage:** all spec sections map to tasks — config (T1), utils (T2), IoTManager full parity incl. `latest_on(search_start)` (T3), KLineManager per-type schema + `read_newest(search_start)` (T4), verification script (T5), docs (T6), credential handoff (T7).
- **No-connection constraint:** `IoTManager.__init__` is lazy; every task's offline verification constructs managers without connecting and asserts `_session is None`.
- **Type consistency:** `latest_on` signature (`search_start` kwarg) is identical between T3 and its T4/T5 consumers; `KLineManager._base_columns` is used by both `create_table` and `upsert`.
- **[VERIFY-ON-SERVER] items** (resolved in the testing phase, never silently changed): `DESCRIBE`-based `table_exists`, `ALTER TABLE RENAME COLUMN`, multi-row `INSERT INTO ... VALUES`, `INNER JOIN`, `GROUP BY TIME(interval)`, window-function subqueries, and `sample_by(fill=)` no-op.
