# QuestManager Design

**Date:** 2026-07-11
**Status:** Approved design

## Objective

Develop `QuestManager` in `core/questdb/questmanager.py`, mimicking the interface patterns of `SqlManager` but adapted for QuestDB -- a high-performance time-series database. The existing skeleton file will be replaced with a full implementation.

## Connection Strategy

Two connection paths, both from the single `QuestManager` class:

| Protocol | Port | Driver | Purpose |
|----------|------|--------|---------|
| PostgreSQL wire | 8812 | `psycopg` (psycopg3) | All DDL, DQL, DML except bulk insert |
| ILP (InfluxDB Line Protocol) | 9009 | `questdb.ingress.Sender` | High-speed bulk `insert()` |

Config is read from `~/.tradedbmgr/config.yaml` using the same `CONFIG` dict the rest of the project uses. Required new keys:

```yaml
questdb_host: localhost
questdb_pg_port: 8812
questdb_ilp_port: 9009
questdb_username: admin
questdb_password: quest
```

## File Structure

```
core/questdb/
  __init__.py          # from .questmanager import QuestManager
  questmanager.py      # QuestManager class (~400 lines)
  utils.py             # infer_questdb_type() and helpers
```

## Dependencies

Add to `pyproject.toml` mandatory dependencies:
- Replace `psycopg2` with `psycopg>=3.1`
- Add `questdb`

## Class Design -- `QuestManager`

### Constructor

```python
class QuestManager:
    def __init__(self):
        self._host = CONFIG.get("questdb_host", "localhost")
        self._pg_port = CONFIG.get("questdb_pg_port", 8812)
        self._ilp_port = CONFIG.get("questdb_ilp_port", 9009)
        self._username = CONFIG.get("questdb_username", "admin")
        self._password = CONFIG.get("questdb_password", "quest")
        self._table_meta: dict[str, dict] = {}
```

### Internal Methods

| Method | Description |
|--------|-------------|
| `_get_conn()` | Returns a new `psycopg.connect()` to QuestDB PG wire |
| `_execute_sql(sql)` | Executes DDL/DML, returns `list[tuple]` or empty |
| `_execute_query(sql)` | Executes SELECT, returns `pd.DataFrame` with column names |
| `_load_table_meta(table_name)` | Caches column info + designated timestamp from `tables()` |
| `_build_where(filter_fields)` | Generates WHERE clause from a filter dict |

### Public Methods -- CRUD

| Method | Key Differences from SqlManager |
|--------|--------------------------------|
| `table_exists(table_name)` | Uses `information_schema.tables` |
| `create_table(...)` | Extra params: `partition_by`, `wal`, `dedup_keys`, `designated_timestamp`, `symbol_columns` |
| `insert(...)` | Dual path: ILP (>1000 rows) or PG wire. Auto-decides based on size |
| `insert_column(table_name, col_name, col_type)` | `ALTER TABLE ... ADD COLUMN ...` |
| `delete_column(table_name, col_name)` | `ALTER TABLE ... DROP COLUMN ...` |
| `rename_column(table_name, old_name, new_name)` | `ALTER TABLE ... RENAME COLUMN ...` |

### Public Methods -- Query

| Method | Notes |
|--------|-------|
| `read_data(table_name, query_fields, filter_fields, unique)` | Basic SELECT; `unique` -> `DISTINCT` |
| `read_range_data(...)` | Timestamp-range filter. Extra `sample_by` for SAMPLE BY |
| `read_data_across_tables(...)` | `join_type="inner"|"asof"`. Default inner join |
| `read_max_in_group(...)` | `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ... DESC)` |
| `read_min_in_group(...)` | Same, `ORDER BY ... ASC` |

### Public Methods -- QuestDB Native

| Method | Notes |
|--------|-------|
| `sample_by(...)` | `SELECT ... SAMPLE BY 1h FILL(PREV)` -- time bucketing |
| `latest_on(...)` | `SELECT * ... LATEST ON ts PARTITION BY symbol` |

### insert() Detail -- Dual Path

```python
def _insert_ilp(self, table_name, df, designated_timestamp, symbol_columns):
    symbols = symbol_columns or []
    with Sender(f"tcp://{self._host}:{self._ilp_port}") as sender:
        for _, row in df.iterrows():
            row_symbols = {col: str(row[col]) for col in symbols if col in df.columns}
            row_columns = {
                col: self._to_ilp_value(row[col])
                for col in df.columns
                if col != designated_timestamp and col not in symbols
            }
            ts = row[designated_timestamp]
            sender.row(table_name, symbols=row_symbols, columns=row_columns, at=ts)
        sender.flush()

def _insert_sql(self, table_name, df):
    cols = list(df.columns)
    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)
    with self._get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
            (tuple(r[1] for r in df.iterrows()))
        )
        conn.commit()
```

## Type Mapping

`core/questdb/utils.py` mirrors `core/sql/utils.py`:

```python
def infer_questdb_type(py_type, is_symbol=False):
    if is_symbol:
        return "SYMBOL"
    if issubclass(py_type, str):
        return "SYMBOL"
    if issubclass(py_type, (int, np.signedinteger)):
        return "LONG" if "64" in str(py_type) else "INT"
    if issubclass(py_type, (np.floating, float)):
        return "DOUBLE"
    if issubclass(py_type, (bool, np.bool_)):
        return "BOOLEAN"
    if issubclass(py_type, (np.datetime64, pd.Timestamp, datetime)):
        return "TIMESTAMP"
    if issubclass(py_type, date):
        return "DATE"
    raise ValueError(f"Unsupported type {py_type}")
```

## Error Handling

- ILP errors -> wrapped in `RuntimeError` with table name context
- PG wire errors -> let `psycopg` exceptions propagate
- Missing table -> check with `table_exists()` before DDL

## Impact Analysis

- **`pyproject.toml`:** Replace `psycopg2` with `psycopg>=3.1`, add `questdb`
- **`SqlManager`:** Switch connection string from `postgresql+psycopg2://` to `postgresql+psycopg://`. No interface changes.
- **`core/questdb/`:** Replace 2 skeleton files with full implementation
- **`core/typedefs.py`:** Reuse `QUERYFIELD_TYPE` / `FILTERFIELD_TYPE` aliases
- **`config.py`:** No changes
