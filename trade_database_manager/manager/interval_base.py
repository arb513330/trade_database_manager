# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : interval_base.py
# @Purpose : Base class for interval-based time-series data management

from collections.abc import Sequence

import pandas as pd

from ..core.iotdb.iotmanager import IoTManager


class IntervalTimeSeriesManager:
    """Manage interval timeseries data, such as K-lines in Apache IoTDB, one table per inst_type + interval.

    Table format: ``{inst_type}_{interval}`` inside the configured IoTDB database,
    e.g. ``tradedata.FUT_1m``, ``tradedata.STK_1d``.

    This is a singleton class. Just call ``IntervalTimeSeriesManager()`` to get the instance.

    :ivar IoTManager qm: The underlying IoTDB connection manager.
    """

    _instance = None
    _qm = None

    def __new__(cls):
        if not isinstance(cls._instance, cls):
            cls._instance = super(IntervalTimeSeriesManager, cls).__new__(cls)
            cls._qm = IoTManager()
            cls._instance.qm = cls._qm
        return cls._instance

    def create_table(
        self,
        table_name: str,
        fields: Sequence[tuple[str, type]],
    ):
        columns = list(fields)
        self.qm.create_table(
            table_name=table_name,
            columns=columns,
            designated_timestamp="timestamp",
            dedup_keys=["timestamp", "full_symbol"],
            symbol_columns=["full_symbol"],
            indexed_columns=["full_symbol"],
        )

    def upsert(self, table_name: str, df: pd.DataFrame):
        assert isinstance(df.index, pd.MultiIndex) and set(df.index.names) == {
            "timestamp",
            "full_symbol",
        }, "DataFrame index must be a MultiIndex with timestamp and full_symbol"

        if not self.qm.table_exists(table_name):
            raise ValueError(f"Table {table_name} does not exist. Please create it first.")

        df_flat = df.reset_index()
        self.qm.insert(
            table_name,
            df_flat,
            designated_timestamp="timestamp",
            symbol_columns=["full_symbol"],
        )

    def read_range(
        self,
        table_name: str,
        symbols: list[str] = None,
        start_time=None,
        end_time=None,
        columns: list[str] = None,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        filter_fields = {"full_symbol": symbols} if symbols else None
        if columns is not None:
            seen = {"timestamp", "full_symbol"}
            query_fields = ["timestamp", "full_symbol"] + [c for c in columns if c not in seen]
        else:
            query_fields = "*"
        result = self.qm.read_range_data(
            table_name,
            query_fields=query_fields,
            start_time=start_time,
            end_time=end_time,
            time_column="timestamp",
            filter_fields=filter_fields,
            timezone=timezone,
        )
        if not result.empty and "timestamp" in result.columns and "full_symbol" in result.columns:
            result = result.set_index(["timestamp", "full_symbol"])
        return result

    def read_newest(
        self,
        table_name: str,
        symbols: list[str] = None,
        columns: list[str] = None,
        search_start=None,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        filter_fields = {"full_symbol": symbols} if symbols else None
        if columns is not None:
            seen = {"timestamp", "full_symbol"}
            query_fields = ["timestamp", "full_symbol"] + [c for c in columns if c not in seen]
        else:
            query_fields = "*"
        result = self.qm.latest_on(
            table_name,
            query_fields=query_fields,
            partition_by="full_symbol",
            timestamp_column="timestamp",
            filter_fields=filter_fields,
            search_start=search_start,
            timezone=timezone,
        )
        if not result.empty and "timestamp" in result.columns and "full_symbol" in result.columns:
            result = result.set_index("full_symbol").rename(columns={"timestamp": "latest_timestamp"})
        return result
