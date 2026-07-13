# @Time    : 2026/7/12 10:33
# @Author  : YQ Tsui
# @File    : kline_questdb.py
# @Purpose : K-line data management on QuestDB

import pandas as pd

from ..typedefs import Interval
from ...core.questdb.questmanager import QuestManager


class KLineManager:
    """Manage OHLCV kline data in QuestDB, one table per instrument type + interval.

    Table format: ``{inst_type}_{interval}``, e.g. ``FUT_1m``, ``STK_1d``.

    :ivar QuestManager qm: The underlying QuestDB connection manager.
    """

    def __init__(self):
        self.qm = QuestManager()

    @staticmethod
    def _infer_partition(interval: Interval) -> str:
        return "MONTH" if interval.value < Interval.DAY.value else "YEAR"

    @staticmethod
    def _table_name(inst_type: str, interval: Interval) -> str:
        return f"{inst_type}_{interval.name}"

    def create_table(self, inst_type: str, interval: Interval):
        partition = self._infer_partition(interval)
        self.qm.create_table(
            table_name=self._table_name(inst_type, interval),
            columns=[
                ("timestamp", pd.Timestamp),
                ("full_symbol", str),
                ("open", float),
                ("high", float),
                ("low", float),
                ("close", float),
                ("volume", float),
                ("money", float),
                ("open_interest", float),
                ("vwap", float),
            ],
            designated_timestamp="timestamp",
            partition_by=partition,
            wal=True,
            dedup_keys=["timestamp", "full_symbol"],
            symbol_columns=["full_symbol"],
        )

    def upsert(self, inst_type: str, interval: Interval, df: pd.DataFrame):

        assert isinstance(df.index, pd.MultiIndex) and set(df.index.names) == {"timestamp", "full_symbol"}, (
            "DataFrame index must be a MultiIndex with timestamp and full_symbol"
        )
        assert set(df.columns) >= {"open", "high", "low", "close", "volume", "money", "open_interest", "vwap"}, (
            "DataFrame must contain all required columns"
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

        result = self.qm.read_range_data(
            table,
            query_fields=columns or "*",
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
        interval: str,
        symbols: list[str] = None,
    ) -> pd.DataFrame:
        table = self._table_name(inst_type, interval)
        filter_fields = {"full_symbol": symbols} if symbols else None

        result = self.qm.latest_on(
            table,
            partition_by="full_symbol",
            timestamp_column="timestamp",
            filter_fields=filter_fields,
        )
        if not result.empty and "timestamp" in result.columns and "full_symbol" in result.columns:
            result = result.set_index(["timestamp", "full_symbol"])
        return result
