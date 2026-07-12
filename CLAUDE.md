# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`trade_database_manager` is a Python library wrapping PostgreSQL and Kdb+ for trade/instrument metadata management. It is in alpha stage and not production-ready. The package provides a unified layer over two backends: SQL (PostgreSQL via SQLAlchemy) and Kdb+ (via pykx). Currently the SQL backend is the primary focus; Kdb+ integration exists for time-series data but is less developed.

## Build & Development Commands

```bash
# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check .

# There are no formal tests — debugging is done via ad-hoc scripts in debug_test/
python debug_test/<script>.py
```

## Architecture

Three-layer design:

1. **`trade_database_manager/config.py`** — Loads runtime config from `~/.tradedbmgr/config.yaml` (contains DB credentials, host/port). This CONFIG dict is imported by both `SqlManager` and `KdbManager`.

2. **`core/`** — Low-level database abstractions, zero domain knowledge:
   - `core/sql/sqlmanager.py` — `SqlManager`: SQLAlchemy-based CRUD, table management, JOIN-based cross-table reads, group-wise extremum queries. Uses `psycopg2` driver with PostgreSQL `ON CONFLICT DO UPDATE` for upserts. Instantiated per-use (not a singleton).
   - `core/kdb/kdbmanager.py` — `KdbManager`: pykx-based Kdb+ connector for splayed/partitioned table reads and writes. Singleton pattern via `instance()` classmethod.
   - `core/typedefs.py` — Shared type aliases (`QUERYFIELD_TYPE`, `FILTERFIELD_TYPE`).

3. **`manager/`** — Domain-specific business logic built on `core/`:
   - `manager/metadata_sql.py` — `MetadataSql` (singleton via `__new__`): Manages the `instruments` table (common metadata) and per-type tables `instruments_{type}`. Handles initialize, upsert, and read — reading does JOINs between common and type-specific tables. Defines `COMMON_METADATA_COLUMNS` and `TYPE_METADATA_COLUMNS` (per instrument type fields).
   - `manager/metadata_sql_cb.py` — `CBMetadataSql(MetadataSql)`: Convertible bond extensions (conversion price history, coupon schedules, cashflow, auxiliary data).
   - `manager/metadata_sql_fut.py` — `FutMetadataSql(MetadataSql)`: Futures extensions (dominant contracts, underlying codes).
   - `manager/fields_data_type.py` — Central SQL column type definitions (`FIELD_DATA_TYPE_SQL` dict mapping field names to SQLAlchemy types). All table creation uses this registry.
   - `manager/typedefs.py` — Domain type aliases: `INST_TYPE_LITERALS` (STK, FUT, OPT, IDX, ETF, LOF, FUND, BOND, CASH, CRYPTO, CB) and `EXCHANGE_LITERALS` (40+ global exchanges).

## Key Patterns

- **Table organization**: Common instrument metadata lives in `instruments`, type-specific columns in `instruments_{type}`. Both tables use composite primary key `(ticker, exchange)`. The `MetadataSql.read_metadata()` method joins them via pandas merge after separate queries.
- **FUT and OPT**: There is commented-out code throughout `metadata_sql.py` that treated FUT and OPT differently (storing all columns including common ones in the type-specific table). The current approach uses the same common + type-specific split as other instrument types — the outer join in `read_metadata()` handles the merge.
- **Upsert strategy**: `SqlManager.insert()` uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` via a custom `pd.DataFrame.to_sql()` method callback. For new tables, unique indexes are auto-created on the DataFrame index columns.
- **Configuration**: All DB connection parameters come from `~/.tradedbmgr/config.yaml`. Use `tools/init_config.py` to generate this file.
- **No formal test suite**: All testing/verification is done via one-off scripts in `debug_test/`. These scripts also serve as reference for data migration workflows (Arctic → SQL, Arctic → Kdb+).

## Tool Scripts

- `tools/init_config.py` — Generate `~/.tradedbmgr/config.yaml` with DB credentials.
- `debug_test/` — Ad-hoc scripts for data migration, table creation, and manual testing. Many import `rqdatac`, `vnpy`, or `arctic` which are not core dependencies.
