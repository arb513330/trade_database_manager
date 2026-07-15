## kline_questdb: Column Filter Parameter

Add `columns` parameter to `read_newest` and fix `read_range` so both ensure
`timestamp` and `full_symbol` are always included in the result.

### Files Touched

- `core/questdb/questmanager.py` — `latest_on` gets `query_fields` param
- `manager/kline/kline_questdb.py` — `read_range` fixed, `read_newest` gets `columns`

### Changes

1. **`QuestManager.latest_on`** — Add `query_fields="*"` parameter, same
   pattern as `read_range_data`. When a list is passed, join it for the
   SELECT clause instead of `*`.

2. **`KLineManager.read_range`** — When `columns` is provided, prepend
   `timestamp` and `full_symbol` before passing to `qm.read_range_data`.
   Use `dict.fromkeys` to preserve order and deduplicate.

3. **`KLineManager.read_newest`** — Add `columns: list[str] = None` with the
   same dedup logic. Fix existing type hint `interval: str` → `interval: Interval`.

### Edge Cases

- Empty `columns` → result has only `timestamp` + `full_symbol`, still valid.
- Result indexing unchanged; with required columns always present, the
  existing `timestamp`/`full_symbol` column checks become reliable no-ops.
