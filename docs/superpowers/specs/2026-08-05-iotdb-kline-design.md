# IoTDB KLine Storage — IoTManager + KLineManager Design

**Date:** 2026-08-05
**Status:** Approved design

## Objective

Replace QuestDB as the KLine (OHLCV) storage backend with Apache IoTDB. Implement:

1. `IoTManager` in `core/iotdb/iotmanager.py` — a full-parity, drop-in replacement for
   `QuestManager` (`core/questdb/questmanager.py`), using the apache-iotdb Python SDK (v2.0.10).
2. `KLineManager` in `manager/kline/kline_iotdb.py` — a clone of
   `manager/kline/kline_questdb.py`, with per-instrument-type column schemas.

**Motivation:** QuestDB community edition cannot do hot/warm/cold tiered storage. IoTDB
supports it natively. Tiered storage is server-side configuration; the client only needs to
write timestamped data normally, so this design needs no special tiering logic.

## Environment

- IoTDB server: `192.168.1.29:6667` (already deployed).
- Python SDK: `apache-iotdb` v2.0.10 installed in the active env.
- Auth: `root/root` fails — server uses custom credentials (to be provided by user).
- Server version unknown until connection; custom TIME-column naming requires ≥ 2.0.8.2.

## File Structure

```
trade_database_manager/
  core/iotdb/
    __init__.py            # exists; unchanged
    iotmanager.py          # NEW — IoTManager (full QuestManager parity)
    utils.py               # NEW — infer_iotdb_type(), to_iotdb_value(), agg-name translation
  manager/kline/
    __init__.py            # exists; add KLineManager export (pattern from kline_questdb)
    kline_iotdb.py         # NEW — KLineManager
debug_test/
  test_kline_iotdb.py      # NEW — round-trip verification against live server
~/.tradedbmgr/config.yaml  # add iotdb_* keys
docs/CLAUDE.md             # add IoTDB modules to architecture docs (minor)
```

## Config

Following the existing `questdb_*` pattern, add to `~/.tradedbmgr/config.yaml`:

```yaml
iotdb_host: "192.168.1.29"
iotdb_port: 6667
iotdb_user: <user-provided>
iotdb_password: <user-provided>
iotdb_database: "tradedata"
```

## Data Model

**Table model** (not tree model). One database `tradedata` holds all kline tables; a table is
`tradedata.{inst_type}_{interval}` (e.g. `tradedata.FUT_1m`, `tradedata.STK_1d`), matching the
QuestDB flat-namespace naming 1:1.

| QuestDB concept | IoTDB table model |
|---|---|
| designated `TIMESTAMP` column | TIME column, named `timestamp` (custom TIME names need server ≥ 2.0.8.2; fallback to native `time` + internal aliasing if older — verified at implementation) |
| `symbol_columns` / `dedup_keys` / `indexed_columns` | TAG columns (TAG = device key; rows with same `(time, tags)` overwrite ⇒ free upsert/dedup) |
| other columns | FIELD columns |
| `partition_by`, `wal` | not applicable — IoTDB auto-partitions by time + device and always WALs; accepted for signature parity and ignored |
| flat namespace | database `tradedata`; manager qualifies every table as `tradedata.{name}` internally |

### Per-Type Column Schema

Common to all types, then per-type extras. `float` → `DOUBLE` (matches QuestDB inference),
`int` → `INT64`, `str` → `STRING`, `bool` → `BOOLEAN`.

```python
KLINE_COMMON_COLUMNS = [
    ("timestamp", pd.Timestamp),
    ("full_symbol", str),
    ("open", float), ("high", float), ("low", float), ("close", float),
    ("volume", float), ("money", float), ("vwap", float),
]

KLINE_TYPE_EXTRA_COLUMNS = {
    "FUT": [("open_interest", float), ("settlement", float)],
    "OPT": [("open_interest", float), ("settlement", float)],
    # STK, ETF, LOF, FUND, IDX, BOND, CB, CASH, CRYPTO → no extras
}
```

`create_table` composes `common + KLINE_TYPE_EXTRA_COLUMNS.get(inst_type, []) + additional_fields`.
So `FUT_1m` carries `open_interest` + `settlement`; `STK_1d` does not.

```sql
CREATE TABLE IF NOT EXISTS tradedata.FUT_1m (
  timestamp TIMESTAMP TIME,
  full_symbol STRING TAG,
  open DOUBLE FIELD, high DOUBLE FIELD, low DOUBLE FIELD, close DOUBLE FIELD,
  volume DOUBLE FIELD, money DOUBLE FIELD, vwap DOUBLE FIELD,
  open_interest DOUBLE FIELD, settlement DOUBLE FIELD
)
```

## IoTManager API (full QuestManager parity)

All `{table}` references below are `tradedata.{table}`; the TIME column is `timestamp`.
`search_start` bounds (inclusive `>=`) the latest-per-symbol search for incremental-update
resume points.

| Method | Implementation |
|---|---|
| `table_exists(t)` | `SHOW TABLES` and match, or `DESCRIBE tradedata.t` in try/except (pick what server supports) |
| `create_table(t, columns, designated_timestamp="timestamp", partition_by=None, wal=None, dedup_keys=None, symbol_columns=None, indexed_columns=None)` | `CREATE TABLE IF NOT EXISTS` with TIME/TAG/FIELD categories derived from schema; parity params accepted and folded into TAG designation / ignored |
| `insert_column(t, name, type)` / `delete_column(t, name)` / `rename_column(t, old, new)` | `ALTER TABLE tradedata.t ADD/DROP/RENAME COLUMN ...` (verify RENAME support) |
| `insert(t, df, designated_timestamp="timestamp", symbol_columns=None, use_ilp=None)` | bulk (>1000 rows): per-symbol `NumpyTablet`; small: multi-row `INSERT INTO ... VALUES (...), (...)` |
| `read_data(t, query_fields="*", filter_fields=None, unique=False)` | `SELECT [DISTINCT] fields FROM tradedata.t WHERE ...` |
| `read_range_data(t, query_fields="*", start_time=None, end_time=None, time_column=None, filter_fields=None, sample_by=None)` | `SELECT fields FROM tradedata.t WHERE timestamp >= X AND timestamp <= Y [AND filters]`; `sample_by` adds `GROUP BY TIME(interval)` |
| `read_data_across_tables(names, join_columns, query_fields="*", filter_fields=None, join_type="inner"\|"asof")` | inner → `INNER JOIN`; asof → fetch both filtered tables + `pd.merge_asof(df0, df1, on=<last join col>, by=<eq cols>, direction="backward")` |
| `read_max_in_group(t, target, group, extremum, filter_fields=None)` / `read_min_in_group(...)` | `SELECT * FROM (SELECT cols, ROW_NUMBER() OVER (PARTITION BY g ORDER BY e DESC\|ASC) AS rn FROM tradedata.t WHERE ...) WHERE rn = 1` |
| `sample_by(t, interval, aggregations=None, timestamp_column=None, filter_fields=None, fill=None, align_to_calendar=True)` | `SELECT AVG(open) AS open, ... FROM tradedata.t WHERE ... GROUP BY TIME(interval)`; agg translation `first/last/min/max/sum/avg/count` → `FIRST_VALUE/LAST_VALUE/MIN_VALUE/MAX_VALUE/SUM/AVG/COUNT`; `fill` → gap-fill where supported, else documented fallback |
| `latest_on(t, partition_by, timestamp_column=None, filter_fields=None, query_fields="*", search_start=None)` | `SELECT fields FROM (SELECT fields, ROW_NUMBER() OVER (PARTITION BY p ORDER BY timestamp DESC) AS rn FROM tradedata.t WHERE timestamp >= {search_start} [AND filters]) WHERE rn = 1` |

### Known gaps / fallbacks (documented, not silent)

- **ASOF JOIN**: no table-model equivalent; implemented via `pd.merge_asof`. Same semantics
  (equality on all but last join column; greatest `<=` on the last).
- **`SAMPLE BY ... FILL(...)`**: no guaranteed 1:1 equivalent. Implement via IoTDB gap-fill where
  the server supports it; otherwise fall back to pandas resample for the kline path.
- **Verification items** (live server): exact `GROUP BY TIME` syntax, `ALTER TABLE RENAME COLUMN`
  support, custom TIME-column-name support (server version).

## Insert Path

- **Bulk** (>1000 rows, mirrors QuestDB ILP threshold): chunk df by `full_symbol` into one
  `NumpyTablet` per symbol (bounded memory, per-device model). Each tablet: `timestamps` int64
  epoch-ms, `column_names = [full_symbol] + FIELD columns present in both df and schema`,
  `data_types` from schema, `column_types = [TAG, FIELD, ...]`, `bitmaps` for NaN/None.
- **Small** (≤1000 rows): multi-row `INSERT INTO tradedata.t (cols) VALUES (...), (...)`;
  strings single-quote-escaped; timestamps as bare literals; NaN → NULL.
- `to_iotdb_value()` normalizes `np.int64/np.float64/np.bool_/pd.Timestamp` and NaN → None,
  mirroring QuestManager `_to_ilp_value`.

## Error Handling

- `TableSession` is persistent; every execute is wrapped in reconnect-and-retry-once on
  `IoTDBConnectionException` / thrift / OSError (survives server restarts).
- Insert raises `RuntimeError("insert failed for tradedata.t (n of N rows sent)")` on partial
  failure, mirroring QuestManager's ILP error contract.
- Table/column names come from internal constants (enums), never raw user input; string filter
  values are escaped (same posture as QuestDB).

## KLineManager (`manager/kline/kline_iotdb.py`)

Near-clone of `kline_questdb.py`:

- `__init__`: `self.qm = IoTManager()`.
- `_table_name(inst_type, interval)` / `_infer_partition` — same; partition passed but ignored.
- `create_table(inst_type, interval, additional_fields=())` — per-type schema above.
- `upsert(inst_type, interval, df)` — asserts MultiIndex `(timestamp, full_symbol)`; validates
  against **common core ∪ type-extra** for that inst_type (no universal `open_interest`
  requirement); `reset_index()`; `qm.insert`.
- `read_range(...)` — `qm.read_range_data` (unchanged shape).
- `read_newest(inst_type, interval, symbols=None, columns=None, search_start=None)` —
  `qm.latest_on(partition_by="full_symbol", search_start=...)`. Symbols with no rows in
  `[search_start, ∞)` are absent from the result (stale-symbol detection).

## Verification (`debug_test/test_kline_iotdb.py`)

1. Connect; `SHOW` server version + databases; create `tradedata` if absent.
2. Round-trip: create `STK_1d` + `FUT_1m`; upsert sample bars for 2+ symbols; exercise
   `read_range`, `read_newest` (with/without `search_start`), `sample_by`, `latest_on`,
   `read_max_in_group`, `read_data` filters.
3. **Dedup proof**: re-upsert identical `(timestamp, full_symbol)` rows → confirm overwrite,
   no duplicates.
4. Confirm tiered storage is server-side only (no client code); note server-side config steps.

## Verified Server Behavior (2026-08-06, live 192.168.1.29:6667)

Round-trip testing confirmed the following server-specific behaviors, which override
the assumptions in the original design sections:

- **Aggregate functions** use standard SQL names (`FIRST`, `LAST`, `MIN`, `MAX`, `SUM`,
  `AVG`, `COUNT`) — the tree-model `*_VALUE` names are NOT valid in the table model.
- **Time bucketing** is `date_bin(interval, ts) AS ts ... GROUP BY 1`; `GROUP BY TIME(...)`
  is not supported (`Unknown function: time`).
- **Latest-per-partition** (`latest_on`) must use a `MAX(ts)` join
  (`SELECT a.* FROM t a INNER JOIN (SELECT part, MAX(ts) AS m FROM t WHERE ... GROUP BY part) b ON a.part=b.part AND a.ts=b.m`) —
  the server rejects an outer `SELECT ... FROM (window-subquery)`.
- **Group extrema** must use a CTE form (`WITH c AS (SELECT ..., ROW_NUMBER() OVER(...) AS rn ...) SELECT ... FROM c WHERE rn=1`).
- **Timestamps** are returned session-timezone-aware (e.g. `+08:00`); `IoTManager`
  strips the tz for QuestDB parity (naive).
- **Inner joins** require qualified columns (`SELECT t0.*, t1.* FROM ... t0 INNER JOIN ... t1 ON ...`);
  bare `SELECT *` is ambiguous.
- **`ALTER TABLE ... RENAME COLUMN` is unsupported** by the server — `rename_column`
  raises `NotImplementedError`. `ADD`/`DROP COLUMN` work.
- `debug_test/test_kline_iotdb.py` is the round-trip verification (needs real
  credentials; not committed, per the project's `debug_test/*` local exclude).
