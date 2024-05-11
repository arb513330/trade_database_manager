from trade_database_manager.core.kdb.kdbmanager import KdbManager
from functools import partial
from loguru import logger
import pandas as pd


def _get_partition_bucket(time_column: pd.Series, bar_freq: str):
    if bar_freq == "tick" or pd.Timedelta(bar_freq) <= pd.Timedelta(seconds=30):
        return time_column.dt.year * 10000 + time_column.dt.month * 100 + time_column.dt.day
    if pd.Timedelta(bar_freq) <= pd.Timedelta(minutes=15):
        return time_column.dt.year // 3
    if pd.Timedelta(bar_freq) <= pd.Timedelta(hours=1):
        return time_column.dt.year // 10
    return None


if __name__ == "__main__":
    kdb_mgr = KdbManager.instance()
    logger.info(f"reading all start")
    df = kdb_mgr.read_partitioned(
        "i1m", path="stk1", partition_func=partial(_get_partition_bucket, bar_freq="1m"),
        #start_time=pd.Timestamp("2021-01-01"), end_time=pd.Timestamp("2023-01-01"),
        other_conditions="ticker=`000606,exchange=`SZSE"
    )
    logger.info(f"reading all done")
    print(df)
    print(df.ticker.unique())

