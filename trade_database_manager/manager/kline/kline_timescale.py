# @Time    : 2026/9/14
# @Author  : YQ Tsui
# @File    : kline_timescale.py
# @Purpose : K-line data management on TimescaleDB

from collections.abc import Sequence

import pandas as pd

from ..typedefs import Interval
from ..interval_base import IntervalTimeSeriesManager

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
    "FUT": [("open_interest", float)],
    "OPT": [("open_interest", float)],
}


class KLineManager:
    """Manage OHLCV kline data in TimescaleDB, one table per inst_type + interval.

    Table format: ``{inst_type}_{interval}`` inside the configured TimescaleDB schema,
    e.g. ``tseries.FUT_1m``, ``tseries.STK_1d``.

    This is a singleton class. Just call ``KLineManager()`` to get the instance.
    """

    _instance = None
    _core = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(KLineManager, cls).__new__(cls)
            cls._core = IntervalTimeSeriesManager()
        return cls._instance

    @property
    def qm(self):
        return self._core.qm

    @staticmethod
    def _infer_partition(interval: Interval) -> str:
        return "MONTH" if interval.value < Interval.DAY.value else "YEAR"

    @staticmethod
    def _table_name(inst_type: str, interval: Interval) -> str:
        return f"{inst_type}_{interval.name}"

    @staticmethod
    def _base_columns(inst_type: str, interval: Interval) -> list[tuple[str, type]]:
        base_columns = KLINE_COMMON_COLUMNS + KLINE_TYPE_EXTRA_COLUMNS.get(inst_type, [])
        return base_columns

    def create_table(
        self,
        inst_type: str,
        interval: Interval,
        additional_fields: Sequence[tuple[str, type]] = (),
    ):
        columns = self._base_columns(inst_type, interval) + list(additional_fields)
        self._core.create_table(self._table_name(inst_type, interval), fields=columns)

    def table_exists(self, inst_type: str, interval: Interval) -> bool:
        """Return whether the kline table for ``inst_type`` and ``interval`` exists."""
        return self.qm.table_exists(self._table_name(inst_type, interval))

    def upsert(self, inst_type: str, interval: Interval, df: pd.DataFrame):
        required = {c for c, _ in self._base_columns(inst_type, interval)} - {"timestamp", "full_symbol"}
        assert set(df.columns) >= required, (
            f"DataFrame must contain all required columns for {inst_type}: {sorted(required)}"
        )

        table = self._table_name(inst_type, interval)
        if not self.table_exists(inst_type, interval):
            self.create_table(inst_type, interval)

        self._core.upsert(table, df)

    def read_range(
        self,
        inst_type: str,
        interval: Interval,
        symbols: list[str] = None,
        start_time=None,
        end_time=None,
        columns: list[str] = None,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        table_name: str = self._table_name(inst_type, interval)
        return self._core.read_range(
            table_name,
            start_time=start_time,
            end_time=end_time,
            symbols=symbols,
            columns=columns,
            timezone=timezone,
        )

    def read_newest(
        self,
        inst_type: str,
        interval: Interval,
        symbols: list[str] = None,
        columns: list[str] = None,
        search_start=None,
        timezone: str | None = None,
    ) -> pd.DataFrame:
        table_name: str = self._table_name(inst_type, interval)
        return self._core.read_newest(
            table_name,
            symbols=symbols,
            columns=columns,
            search_start=search_start,
            timezone=timezone,
        )
