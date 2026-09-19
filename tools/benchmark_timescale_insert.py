#!/usr/bin/env python3
"""Benchmark legacy pandas.to_sql upserts vs COPY staging inserts for TimescaleDB."""

from __future__ import annotations

import statistics
import time
from functools import partial

import numpy as np
import pandas as pd

from trade_database_manager.core.timescale.timescalemanager import TimescaleManager, _insert_on_conflict_update


def build_df(rows: int) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=rows, freq="s")
    symbols = [f"SYM{idx % 50:02d}" for idx in range(rows)]
    return pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": symbols,
            "open": np.linspace(100.0, 200.0, rows),
            "high": np.linspace(101.0, 210.0, rows),
            "low": np.linspace(99.0, 190.0, rows),
            "close": np.linspace(100.5, 205.5, rows),
            "volume": np.arange(rows, dtype=np.int64) % 1000,
            "turnover": (np.arange(rows, dtype=np.float64) % 97) * 2.5,
        }
    )


def drop_table(manager: TimescaleManager, table_name: str) -> None:
    try:
        manager._execute(f'DROP TABLE IF EXISTS {manager._q(table_name)}')
    except Exception:
        pass


def benchmark_old_path(manager: TimescaleManager, table_name: str, df: pd.DataFrame) -> float:
    drop_table(manager, table_name)
    manager.create_table(
        table_name=table_name,
        columns=[
            ("timestamp", pd.Timestamp),
            ("symbol", str),
            ("open", float),
            ("high", float),
            ("low", float),
            ("close", float),
            ("volume", int),
            ("turnover", float),
        ],
        designated_timestamp="timestamp",
        dedup_keys=["timestamp", "symbol"],
        symbol_columns=["symbol"],
        indexed_columns=["symbol"],
    )

    def run() -> int:
        prepared = manager._prepare_timestamp_column(df.copy(), "timestamp")
        method = partial(_insert_on_conflict_update, indexes=["timestamp", "symbol"])
        prepared.to_sql(
            table_name,
            manager._engine,
            schema=manager._schema,
            if_exists="append",
            index=False,
            method=method,
            chunksize=2048,
        )
        return len(prepared)

    start = time.perf_counter()
    run()
    return time.perf_counter() - start


def benchmark_new_path(manager: TimescaleManager, table_name: str, df: pd.DataFrame) -> float:
    drop_table(manager, table_name)
    manager.create_table(
        table_name=table_name,
        columns=[
            ("timestamp", pd.Timestamp),
            ("symbol", str),
            ("open", float),
            ("high", float),
            ("low", float),
            ("close", float),
            ("volume", int),
            ("turnover", float),
        ],
        designated_timestamp="timestamp",
        dedup_keys=["timestamp", "symbol"],
        symbol_columns=["symbol"],
        indexed_columns=["symbol"],
    )

    start = time.perf_counter()
    manager.insert(table_name, df, designated_timestamp="timestamp", symbol_columns=["symbol"])
    return time.perf_counter() - start


def main() -> None:
    manager = TimescaleManager()
    for rows in (10_000, 50_000):
        table_old = f"bench_old_{rows}"
        table_new = f"bench_new_{rows}"
        df = build_df(rows)

        old_times = [benchmark_old_path(manager, table_old, df) for _ in range(1)]
        new_times = [benchmark_new_path(manager, table_new, df) for _ in range(1)]

        print(f"rows={rows:,}")
        print(f"  legacy to_sql: {statistics.mean(old_times):.2f}s")
        print(f"  copy staging: {statistics.mean(new_times):.2f}s")
        if statistics.mean(old_times) > 0:
            speedup = statistics.mean(old_times) / statistics.mean(new_times)
            print(f"  speedup: {speedup:.2f}x")
        print()

        drop_table(manager, table_old)
        drop_table(manager, table_new)


if __name__ == "__main__":
    main()
