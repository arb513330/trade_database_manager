from trade_database_manager.core.kdb.kdbmanager import KdbManager
import pandas as pd
from arctic.date import DateRange
from arctic import Arctic
from arctic.auth import Credential
from arctic.hooks import register_get_auth_hook

# from trade_database_manager.manager import MetadataSql
from loguru import logger
from functools import partial
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def _get_partition_bucket(time_column: pd.Series, bar_freq: str):
    if bar_freq == "tick" or pd.Timedelta(bar_freq) <= pd.Timedelta(seconds=30):
        return time_column.dt.year * 10000 + time_column.dt.month * 100 + time_column.dt.day
    if pd.Timedelta(bar_freq) <= pd.Timedelta(minutes=15):
        return time_column.dt.year // 3
    if pd.Timedelta(bar_freq) <= pd.Timedelta(hours=1):
        return time_column.dt.year // 10
    return None


def get_bar_data(barlib, limit_lib, symbol, exchange, interval="1d", start=None, end=None):
    interval = "d" if interval == "1d" else interval
    bar_df = barlib.read(f"{symbol}_{exchange}_{interval}", chunk_range=DateRange(start, end))
    bar_df = bar_df.drop(columns=["order_book_id", "open_interest"], errors="ignore")
    if interval == "1d":
        limit_df = limit_lib.read(f"{symbol}_{exchange}", chunk_range=DateRange(start, end))
        assert bar_df.index.difference(limit_df.index).empty
        df = pd.merge(bar_df, limit_df, left_index=True, right_index=True, how="left").drop(
            columns=["order_book_id", "open_interest"], errors="ignore"
        )
        df.limit_up.ffill(inplace=True)
        df.limit_down.ffill(inplace=True)
    else:
        df = bar_df
    df.index.name = "datetime"
    return df


if __name__ == "__main__":
    user_dir = Path.home() / ".vntrader"
    with open(user_dir / "vt_setting.json", "r") as f:
        SETTINGS = json.load(f)

    def arctic_auth_hook(*_):
        if bool(SETTINGS.get("database.password", "")) and bool(SETTINGS.get("database.user", "")):
            return Credential(
                database="admin",
                user=SETTINGS["database.user"],
                password=SETTINGS["database.password"],
            )
        return None

    register_get_auth_hook(arctic_auth_hook)

    arctic_store = Arctic(SETTINGS["database.host"], tz_aware=True, tzinfo=ZoneInfo(SETTINGS["database.timezone"]))
    barlib = arctic_store.get_library("bar_data")
    limit_lib = arctic_store.get_library("limit_up_down")
    metalib = arctic_store.get_library("stock_meta")

    # all_cn_stock = MetadataSql().read_metadata_for_insttype(
    #     "STK", exchange=["SSE", "SZSE"], query_fields=["ticker", "exchange", "delisted_date"]
    # )
    # all_cn_stock = all_cn_stock[all_cn_stock.delisted_date > "2013-12-31"].sort_index(level="ticker")
    all_stock = sorted([x for x in metalib.list_symbols() if "SZSE" in x or "SSE" in x])
    all_stock = [x.split("_") for x in all_stock]

    kdb_mgr = KdbManager.instance()

    errors = []
    for ticker, exch in all_stock[0:1000]:
        interval = "1m"
        try:
            logger.info(f"reading {ticker} {exch} {interval}")
            df = get_bar_data(barlib, limit_lib, ticker, exch, interval=interval)
            df["ticker"] = ticker
            df["exchange"] = exch
            logger.info(f"writing {ticker} {exch} {interval}")
            kdb_mgr.write_partitioned(
                f"i1m",
                df,
                path="stk1",
                partition_func=partial(_get_partition_bucket, bar_freq="1m"),
                key_column="datetime",
            )
            logger.info(f"writing done {ticker} {exch} {interval}")
        except Exception:
            logger.info(f"error {ticker} {exch} {interval}")
            errors.append((ticker, exch))

    print(errors)
