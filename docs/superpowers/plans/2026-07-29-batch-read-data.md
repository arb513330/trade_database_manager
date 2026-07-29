# Batch Query for Large Filter Lists in SqlManager

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `OperationalError` when `SqlManager.read_data()` (and siblings) receives large `filter_fields` lists that cause psycopg to exceed the 65,535 SQL parameter limit.

**Architecture:** Add internal batching inside `SqlManager` methods (`read_data`, `read_range_data`, `read_data_across_tables`) so that when a filter field contains more values than can fit in a single parameterized query, the query is transparently split into smaller chunks, executed separately, and the results concatenated. No public API changes.

**Tech Stack:** SQLAlchemy, psycopg, Pandas, PostgreSQL

---

## File Structure

- **Modify:** `build/lib/trade_database_manager/core/sql/sqlmanager.py`
- **Create:** `tests/test_sqlmanager_batching.py`

---

### Task 1: Add `_MAX_SQL_PARAMS` constant, `_build_conditions` helper, and `_read_data_batched` method

**Files:**
- Modify: `build/lib/trade_database_manager/core/sql/sqlmanager.py`
- Create: `tests/test_sqlmanager_batching.py`

- [ ] **Step 1: Add `_MAX_SQL_PARAMS` constant after imports**

```python
_MAX_SQL_PARAMS = 65534  # psycopg hard limit is 65535; leave 1 for safety
```

- [ ] **Step 2: Add `_build_conditions` static method to `SqlManager`**

```python
    @staticmethod
    def _build_conditions(table, filter_fields):
        """Build WHERE conditions from filter_fields dict."""
        conditions = []
        for field, filter_values in filter_fields.items():
            if isinstance(filter_values, Container) and not isinstance(filter_values, str | bytes):
                conditions.append(table.columns[field].in_(filter_values))
            else:
                conditions.append(table.columns[field] == filter_values)
        return conditions
```

- [ ] **Step 3: Add `_read_data_batched` method to `SqlManager`**

```python
    def _read_data_batched(self, table, query_fields, filter_fields, stmt_builder):
        """Execute a read query with automatic batching of large IN-clause values.

        :param table: SQLAlchemy Table object
        :param query_fields: list of Column objects (already resolved) or Table (for `*`)
        :param filter_fields: dict of field_name -> values for filtering
        :param stmt_builder: callable (base_stmt) -> stmt. Receives a pre-built
            select with query_fields and WHERE conditions already applied, and
            applies additional clauses (DISTINCT, time range, etc.).
        :return: pd.DataFrame
        """
        if not filter_fields:
            stmt = select(*query_fields) if not isinstance(query_fields, Table) else select(query_fields)
            stmt = stmt_builder(stmt)
            res = self._execute(stmt)
            return pd.DataFrame(res.fetchall(), columns=res.keys())

        # Find the filter field with the largest IN-list
        max_len = 0
        max_field = None
        max_values = None
        for field, values in filter_fields.items():
            if isinstance(values, Container) and not isinstance(values, str | bytes):
                length = len(values)
                if length > max_len:
                    max_len = length
                    max_field = field
                    max_values = values

        if max_len <= _MAX_SQL_PARAMS:
            conditions = self._build_conditions(table, filter_fields)
            stmt = select(*query_fields) if not isinstance(query_fields, Table) else select(query_fields)
            stmt = stmt.where(and_(*conditions))
            stmt = stmt_builder(stmt)
            res = self._execute(stmt)
            return pd.DataFrame(res.fetchall(), columns=res.keys())

        # Batching: chunk the largest IN-list
        chunks = [max_values[i : i + _MAX_SQL_PARAMS] for i in range(0, max_len, _MAX_SQL_PARAMS)]
        frames = []
        for chunk in chunks:
            chunk_filter_fields = dict(filter_fields)
            chunk_filter_fields[max_field] = chunk
            conditions = self._build_conditions(table, chunk_filter_fields)
            stmt = select(*query_fields) if not isinstance(query_fields, Table) else select(query_fields)
            stmt = stmt.where(and_(*conditions))
            stmt = stmt_builder(stmt)
            res = self._execute(stmt)
            frames.append(pd.DataFrame(res.fetchall(), columns=res.keys()))

        return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 4: Rewrite `read_data` to use `_read_data_batched`**

```python
    def read_data(self, table_name: str, query_fields: QUERYFIELD_TYPE = "*", filter_fields=None, unique=False):
        """... (existing docstring) ..."""
        meta = MetaData()
        table = Table(table_name, meta, autoload_with=self.engine)

        if query_fields != "*":
            query_fields = [table.columns[field] for field in query_fields]
        else:
            query_fields = table

        def stmt_builder(stmt):
            if unique:
                stmt = stmt.distinct()
            return stmt

        return self._read_data_batched(table, query_fields, filter_fields, stmt_builder)
```

- [ ] **Step 5: Commit**

```bash
git add build/lib/trade_database_manager/core/sql/sqlmanager.py
git commit -m "refactor: extract condition builder, add _read_data_batched to SqlManager"
```

---

### Task 2: Rewrite `read_range_data` to use batching

**Files:**
- Modify: `build/lib/trade_database_manager/core/sql/sqlmanager.py`

- [ ] **Step 1: Rewrite `read_range_data`**

```python
    def read_range_data(
        self, table_name, query_fields="*", start_time=None, end_time=None,
        time_column="timestamp", filter_fields=None,
    ):
        """... (existing docstring) ..."""
        meta = MetaData()
        table = Table(table_name, meta, autoload_with=self.engine)

        if query_fields != "*":
            query_fields = [table.columns[field] for field in query_fields]
        else:
            query_fields = table

        if not filter_fields:
            filter_fields = {}

        def stmt_builder(stmt):
            time_conditions = []
            if start_time:
                time_conditions.append(getattr(table.c, time_column, table.c.end_time) >= start_time)
            if end_time:
                time_conditions.append(getattr(table.c, time_column, table.c.start_time) <= end_time)
            if time_conditions:
                stmt = stmt.where(and_(*time_conditions))
            return stmt

        return self._read_data_batched(table, query_fields, filter_fields, stmt_builder)
```

- [ ] **Step 2: Commit**

```bash
git add build/lib/trade_database_manager/core/sql/sqlmanager.py
git commit -m "refactor: use _read_data_batched in read_range_data"
```

---

### Task 3: Rewrite `read_data_across_tables` to use batching

**Files:**
- Modify: `build/lib/trade_database_manager/core/sql/sqlmanager.py`

- [ ] **Step 1: Add `_build_conditions_across_tables` static method**

```python
    @staticmethod
    def _build_conditions_across_tables(tables, filter_fields):
        conditions = []
        for table_name, field_dict in filter_fields.items():
            table = tables[table_name]
            for field, values in field_dict.items():
                if isinstance(values, Container) and not isinstance(values, str | bytes):
                    conditions.append(table.columns[field].in_(values))
                else:
                    conditions.append(table.columns[field] == values)
        return conditions
```

- [ ] **Step 2: Add `_read_data_across_tables_batched` method**

```python
    def _read_data_across_tables_batched(self, tables, query_fields_rel, filter_fields, stmt_builder):
        if not filter_fields:
            stmt = select(*query_fields_rel)
            stmt = stmt_builder(stmt)
            res = self._execute(stmt)
            return pd.DataFrame(res.fetchall(), columns=res.keys())

        max_len = 0
        max_key = None
        max_values = None
        for table_name, field_dict in filter_fields.items():
            table = tables[table_name]
            for field, values in field_dict.items():
                if isinstance(values, Container) and not isinstance(values, str | bytes):
                    length = len(values)
                    if length > max_len:
                        max_len = length
                        max_key = (table_name, field)
                        max_values = values

        if max_len <= _MAX_SQL_PARAMS:
            stmt = select(*query_fields_rel)
            stmt = stmt_builder(stmt)
            conditions = self._build_conditions_across_tables(tables, filter_fields)
            if conditions:
                stmt = stmt.where(and_(*conditions))
            res = self._execute(stmt)
            return pd.DataFrame(res.fetchall(), columns=res.keys())

        chunks = [max_values[i : i + _MAX_SQL_PARAMS] for i in range(0, max_len, _MAX_SQL_PARAMS)]
        frames = []
        for chunk in chunks:
            chunk_filter_fields = dict(filter_fields)
            chunk_table, chunk_field = max_key
            chunk_filter_fields[chunk_table] = dict(filter_fields[chunk_table])
            chunk_filter_fields[chunk_table][chunk_field] = chunk
            stmt = select(*query_fields_rel)
            stmt = stmt_builder(stmt)
            conditions = self._build_conditions_across_tables(tables, chunk_filter_fields)
            if conditions:
                stmt = stmt.where(and_(*conditions))
            res = self._execute(stmt)
            frames.append(pd.DataFrame(res.fetchall(), columns=res.keys()))

        return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 3: Rewrite `read_data_across_tables`**

```python
    def read_data_across_tables(
        self, table_names, joined_columns, query_fields="*", filter_fields=None, unique=False,
    ):
        """... (existing docstring) ..."""
        meta = MetaData()
        tables = {table_name: Table(table_name, meta, autoload_with=self.engine) for table_name in table_names}

        joined_table = reduce(
            lambda x, y: x.join(y, and_(*[x.columns[col] == y.columns[col] for col in joined_columns])),
            tables.values(),
        )

        query_fields_rel = []
        if query_fields != "*":
            for table_name, colnames in query_fields.items():
                if isinstance(colnames, str):
                    query_fields_rel.append(tables[table_name].columns[colnames])
                else:
                    query_fields_rel.extend([tables[table_name].columns[colname] for colname in colnames])
        else:
            query_fields_rel = [text("*")]

        def stmt_builder(stmt):
            if unique:
                stmt = stmt.distinct()
            return stmt.select_from(joined_table)

        return self._read_data_across_tables_batched(tables, query_fields_rel, filter_fields, stmt_builder)
```

- [ ] **Step 4: Commit**

```bash
git add build/lib/trade_database_manager/core/sql/sqlmanager.py
git commit -m "refactor: use batching in read_data_across_tables"
```

---

### Task 4: Add unit tests for batching

**Files:**
- Create: `tests/test_sqlmanager_batching.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for batching behavior in SqlManager read methods."""

from unittest.mock import MagicMock, patch
import pandas as pd
from sqlalchemy import Column, Table, MetaData, String

from trade_database_manager.core.sql.sqlmanager import SqlManager, _MAX_SQL_PARAMS


class TestReadDataBatched:
    """Verify _read_data_batched splits large IN lists correctly."""

    def test_no_batching_under_limit(self):
        mgr = SqlManager()
        meta = MetaData()
        table = Table("test", meta, Column("ticker", String))
        filter_fields = {"ticker": ["A", "B", "C"]}

        with patch.object(mgr, "_execute") as mock_exec:
            mock_exec.return_value = MagicMock(fetchall=MagicMock(return_value=[("A",)]), keys=MagicMock(return_value=["ticker"]))
            mgr._read_data_batched(table, [table.columns["ticker"]], filter_fields, lambda s: s)

        assert mock_exec.call_count == 1

    def test_batching_over_limit(self):
        mgr = SqlManager()
        meta = MetaData()
        table = Table("test", meta, Column("ticker", String))
        huge_list = [f"TICKER_{i}" for i in range(_MAX_SQL_PARAMS + 100)]
        filter_fields = {"ticker": huge_list}

        call_count = [0]

        def mock_execute(stmt):
            call_count[0] += 1
            return MagicMock(fetchall=MagicMock(return_value=[("X",)]), keys=MagicMock(return_value=["ticker"]))

        with patch.object(mgr, "_execute", side_effect=mock_execute):
            result = mgr._read_data_batched(table, [table.columns["ticker"]], filter_fields, lambda s: s)

        assert call_count[0] == 2

    def test_no_filter_fields_executes_once(self):
        mgr = SqlManager()
        meta = MetaData()
        table = Table("test", meta, Column("ticker", String))

        with patch.object(mgr, "_execute") as mock_exec:
            mock_exec.return_value = MagicMock(fetchall=MagicMock(return_value=[]), keys=MagicMock(return_value=["ticker"]))
            mgr._read_data_batched(table, table, None, lambda s: s)
            mgr._read_data_batched(table, table, {}, lambda s: s)

        assert mock_exec.call_count == 2

    def test_read_data_delegates_to_batched(self):
        mgr = SqlManager()
        with patch.object(mgr, "_read_data_batched") as mock_batched:
            mock_batched.return_value = pd.DataFrame()
            mgr.read_data("instruments", query_fields=["ticker"], filter_fields={"ticker": ["A"]})
        mock_batched.assert_called_once()

    def test_batched_results_concatenated(self):
        mgr = SqlManager()
        meta = MetaData()
        table = Table("test", meta, Column("ticker", String))
        huge_list = [f"TICKER_{i}" for i in range(_MAX_SQL_PARAMS + 50)]
        filter_fields = {"ticker": huge_list}

        batch_num = [0]

        def mock_execute(stmt):
            batch_num[0] += 1
            return MagicMock(fetchall=MagicMock(return_value=[(f"TICKER_{batch_num[0] * 100}",)]), keys=MagicMock(return_value=["ticker"]))

        with patch.object(mgr, "_execute", side_effect=mock_execute):
            result = mgr._read_data_batched(table, [table.columns["ticker"]], filter_fields, lambda s: s)

        assert len(result) == 2
```

- [ ] **Step 2: Run tests**

```bash
cd D:\Documents\TradeResearch\trade_database_manager
F:\Applications\miniconda3\envs\vnpy13\python.exe -m pytest tests/test_sqlmanager_batching.py -v --tb=short 2>&1
```

Expected: 5 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_sqlmanager_batching.py
git commit -m "test: add unit tests for SqlManager query batching"
```

---

### Task 5: End-to-end smoke test

- [ ] **Step 1: Run `MetadataSql().read_metadata()` in the installed package**

```bash
F:\Applications\miniconda3\envs\vnpy13\python.exe -c "from trade_database_manager.manager.metadata_sql.metadata_sql import MetadataSql; dfs = MetadataSql().read_metadata(); print('OK:', len(dfs), 'instrument types', {k: len(v) for k, v in dfs.items()})" 2>&1
```

Expected: Prints instrument types and row counts, no `OperationalError`.
