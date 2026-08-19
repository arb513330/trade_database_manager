"""Tests for batching behavior in SqlManager read methods."""

import sys
sys.path.insert(0, r"D:\Documents\TradeResearch\trade_database_manager\build\lib")

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
            mock_exec.return_value = MagicMock(
                fetchall=MagicMock(return_value=[("A",)]),
                keys=MagicMock(return_value=["ticker"]),
            )
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
            return MagicMock(
                fetchall=MagicMock(return_value=[("X",)]),
                keys=MagicMock(return_value=["ticker"]),
            )

        with patch.object(mgr, "_execute", side_effect=mock_execute):
            mgr._read_data_batched(table, [table.columns["ticker"]], filter_fields, lambda s: s)

        assert call_count[0] == 2

    def test_no_filter_fields_executes_once(self):
        mgr = SqlManager()
        meta = MetaData()
        table = Table("test", meta, Column("ticker", String))

        with patch.object(mgr, "_execute") as mock_exec:
            mock_exec.return_value = MagicMock(
                fetchall=MagicMock(return_value=[]),
                keys=MagicMock(return_value=["ticker"]),
            )
            mgr._read_data_batched(table, [table.columns["ticker"]], None, lambda s: s)
            mgr._read_data_batched(table, [table.columns["ticker"]], {}, lambda s: s)

        assert mock_exec.call_count == 2

    def test_read_data_delegates_to_batched(self):
        mgr = object.__new__(SqlManager)
        with patch("trade_database_manager.core.sql.sqlmanager.Table") as _mock_table, \
             patch("trade_database_manager.core.sql.sqlmanager.MetaData"):
            with patch.object(mgr, "_read_data_batched") as mock_batched:
                mock_batched.return_value = pd.DataFrame()
                # read_data needs engine on the instance for autoload_with
                mgr.engine = MagicMock()
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
            return MagicMock(
                fetchall=MagicMock(return_value=[(f"TICKER_{batch_num[0] * 100}",)]),
                keys=MagicMock(return_value=["ticker"]),
            )

        with patch.object(mgr, "_execute", side_effect=mock_execute):
            result = mgr._read_data_batched(table, [table.columns["ticker"]], filter_fields, lambda s: s)

        assert len(result) == 2
