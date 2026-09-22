import pandas as pd

from trade_database_manager.core.sql.sqlmanager import SqlManager


def test_insert_chunk_size_is_reduced_for_large_string_rows():
    df = pd.DataFrame({
        "ticker": ["X" * 5_000_000 for _ in range(20)],
        "value": list(range(20)),
    })

    chunk_size = SqlManager._estimate_insert_chunk_size(df, 2048)
    assert chunk_size < 2048
    assert chunk_size >= 1


def test_insert_chunk_size_keeps_small_rows_large():
    df = pd.DataFrame({
        "ticker": ["ABC" for _ in range(20)],
        "value": list(range(20)),
    })

    chunk_size = SqlManager._estimate_insert_chunk_size(df, 2048)
    assert chunk_size == 2048
