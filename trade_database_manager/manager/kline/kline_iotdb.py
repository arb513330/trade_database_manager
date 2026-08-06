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
