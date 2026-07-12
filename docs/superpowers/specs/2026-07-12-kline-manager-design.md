# KLineManager Design

**Date:** 2026-07-12
**Status:** Approved design

## Objective

Develop `KLineManager` in `manager/kline/kline_questdb.py`, wrapping `QuestManager` to manage OHLCV kline data in QuestDB. One table per instrument type + interval, with automatic partitioning and idempotent upsert.

## File Structure

```
manager/kline/
  __init__.py              # from .kline_questdb import KLineManager
  kline_questdb.py         # KLineManager class (~120 lines)
```

Also update `manager/__init__.py` to export `KLineManager`.

## Dependencies

- `QuestManager` from `core/questdb/questmanager.py`
- `pd.DataFrame` with MultiIndex `(datetime, full_symbol)` and columns `open, high, low, close, volume, money, open_interest, vwap`

## Table Schema

```sql
CREATE TABLE IF NOT EXISTS {inst_type}_{interval} (
    timestamp TIMESTAMP,
    full_symbol SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    money DOUBLE,
    open_interest DOUBLE,
    vwap DOUBLE
) TIMESTAMP(timestamp) PARTITION BY {YEAR|MONTH} WAL
DEDUP UPSERT KEYS(timestamp, full_symbol);
```

### Partition Strategy

| Interval | Partition By | Rationale |
|----------|-------------|-----------|
| `1d`, `1w`, `1M` and longer | `YEAR` | Daily/weekly bars produce manageable partition counts across decades |
| `1m`, `5m`, `15m`, `30m`, `1h` etc. | `MONTH` | Minute bars need finer granularity to avoid oversized partitions |

### Instrument Types

From `manager/typedefs.py`: `STK`, `ETF`, `LOF`, `FUT`, `OPT`, `FUND`, `CB`.

### full_symbol Format

`<code>.<exchange>` — e.g. `rb2401.SHFE`, `000001.SSE`, `AAPL.SMART`.

## Class Design — `KLineManager`

### Constructor

```python
class KLineManager:
    def __init__(self):
        self.qm = QuestManager()
```

### Public Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_table` | `(inst_type: str, interval: str)` | CREATE TABLE IF NOT EXISTS with inferred partition |
| `upsert` | `(inst_type: str, interval: str, df: pd.DataFrame)` | Flatten MultiIndex, auto-create table if missing, insert via QuestManager |
| `read_range` | `(inst_type, interval, symbols=None, start_time=None, end_time=None, columns=None) -> pd.DataFrame` | Time-range read with optional symbol filter and column selection |
| `read_newest` | `(inst_type, interval, symbols=None) -> pd.DataFrame` | LATEST ON timestamp PARTITION BY full_symbol, optional symbol filter |

### Internal Helper

```python
@staticmethod
def _infer_partition(interval: str) -> str:
    """Infer PARTITION BY strategy from interval string."""
    if interval.endswith(("d", "w", "M")):
        return "YEAR"
    return "MONTH"
```

### `upsert()` Detail

```python
def upsert(self, inst_type: str, interval: str, df: pd.DataFrame):
    table = f"{inst_type}_{interval}"
    if not self.qm.table_exists(table):
        self.create_table(inst_type, interval)

    # Flatten MultiIndex (datetime, full_symbol) -> columns
    df_flat = df.reset_index()

    self.qm.insert(
        table,
        df_flat,
        designated_timestamp="timestamp",
        symbol_columns=["full_symbol"],
    )
```

MultiIndex level names must be `datetime` and `full_symbol`. After `reset_index()`, they become regular columns matching the table schema. `QuestManager.insert()` auto-decides ILP vs PG wire based on row count.

### `read_range()` Detail

```python
def read_range(
    self, inst_type, interval,
    symbols=None, start_time=None, end_time=None,
    columns=None,
) -> pd.DataFrame:
    table = f"{inst_type}_{interval}"
    filter_fields = {"full_symbol": symbols} if symbols else None

    result = self.qm.read_range_data(
        table,
        query_fields=columns if columns else "*",
        start_time=start_time,
        end_time=end_time,
        time_column="timestamp",
        filter_fields=filter_fields,
    )
    if not result.empty:
        result = result.set_index(["timestamp", "full_symbol"])
    return result
```

Returns DataFrame with MultiIndex `(timestamp, full_symbol)`, matching the input format of `upsert()`.

### `read_newest()` Detail

```python
def read_newest(self, inst_type, interval, symbols=None) -> pd.DataFrame:
    table = f"{inst_type}_{interval}"
    filter_fields = {"full_symbol": symbols} if symbols else None

    result = self.qm.latest_on(
        table,
        partition_by="full_symbol",
        timestamp_column="timestamp",
        filter_fields=filter_fields,
    )
    if not result.empty:
        result = result.set_index(["timestamp", "full_symbol"])
    return result
```

## Export

`manager/kline/__init__.py`:
```python
from .kline_questdb import KLineManager
```

`manager/__init__.py` — add:
```python
from .kline.kline_questdb import KLineManager
```

## Error Handling

- Table not found on read → empty DataFrame returned (QuestManager handles)
- Duplicate key on insert → handled by `DEDUP UPSERT KEYS` at table level
- Missing MultiIndex levels → pandas `reset_index()` raises if index names don't match expected

## Testing

Ad-hoc script in `debug_test/`:
1. Create table for `FUT_1m`
2. Generate synthetic OHLCV data for 2 symbols * 100 bars
3. `upsert()` into table
4. `read_range()` and verify row counts
5. `read_newest()` and verify one row per symbol
6. `upsert()` overlapping data and verify dedup works

## Impact Analysis

- **`manager/kline/kline_questdb.py`:** Replace skeleton with full implementation (~120 lines)
- **`manager/kline/__init__.py`:** Add export
- **`manager/__init__.py`:** Add KLineManager to exports
- **`core/questdb/`:** No changes — reused as-is
- **Other files:** No changes
