# @Time    : 2026/8/5
# @Author  : YQ Tsui
# @File    : utils.py
# @Purpose : IoTDB type inference and value conversion utilities

from datetime import date, datetime

import numpy as np
import pandas as pd

# QuestDB-style aggregate names -> IoTDB table-model aggregate functions.
# The table model uses standard SQL aggregate names (FIRST/LAST/MIN/MAX/SUM/
# AVG/COUNT) — NOT the tree-model *_VALUE names.
AGG_FUNC_MAP = {
    "first": "FIRST",
    "last": "LAST",
    "min": "MIN",
    "max": "MAX",
    "sum": "SUM",
    "avg": "AVG",
    "mean": "AVG",
    "count": "COUNT",
}
_KNOWN_AGG_VALUES = frozenset(AGG_FUNC_MAP.values())


def infer_iotdb_type(py_type) -> str:
    """Infer an IoTDB table-model column type from a Python type."""
    if py_type is None:
        raise ValueError(f"Unsupported type {py_type}")
    if issubclass(py_type, (bool, np.bool_)):
        return "BOOLEAN"
    if issubclass(py_type, (int, np.integer)):
        return "INT64"
    if issubclass(py_type, (float, np.floating)):
        return "DOUBLE"
    if issubclass(py_type, str):
        return "STRING"
    if issubclass(py_type, (np.datetime64, pd.Timestamp, datetime)):
        return "TIMESTAMP"
    if issubclass(py_type, date):
        return "DATE"
    raise ValueError(f"Unsupported type {py_type}")


def to_iotdb_value(value):
    """Normalize a Python/numpy/pandas scalar for IoTDB insertion; NaN -> None."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if pd.isna(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.datetime64):
        return None if pd.isna(value) else pd.Timestamp(value).to_pydatetime()
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def translate_agg_func(func: str) -> str:
    """Map a QuestDB-style aggregate name to the IoTDB table-model equivalent."""
    upper = func.strip().upper()
    if upper in _KNOWN_AGG_VALUES:
        return upper
    mapped = AGG_FUNC_MAP.get(func.strip().lower())
    if mapped is None:
        raise ValueError(f"Unsupported aggregate function: {func}")
    return mapped


def format_time_literal(ts) -> str:
    """Format a datetime-like as an IoTDB bare timestamp literal (unquoted)."""
    return pd.Timestamp(ts).to_pydatetime().strftime("%Y-%m-%d %H:%M:%S")


def escape_string(value) -> str:
    """Escape a string literal for embedding in IoTDB SQL."""
    return str(value).replace("'", "''")
