# @Time    : 2026/7/11 19:00
# @Author  : YQ Tsui
# @File    : utils.py
# @Purpose : QuestDB type inference utilities

from datetime import date, datetime

import numpy as np
import pandas as pd


def infer_questdb_type(py_type, is_symbol=False):
    """
    Infer QuestDB column type from a Python type.

    :param py_type: A Python type (str, int, float, bool, datetime, etc.)
    :param is_symbol: If True, treat string types as SYMBOL (default False).
    :type is_symbol: bool
    :return: A QuestDB type name string (e.g. "SYMBOL", "DOUBLE", "TIMESTAMP").
    :rtype: str
    :raises ValueError: If the type is not supported.
    """
    if is_symbol:
        return "SYMBOL"
    if issubclass(py_type, str):
        return "SYMBOL"
    if issubclass(py_type, (int, np.signedinteger)):
        return "LONG" if "64" in str(py_type) else "INT"
    if issubclass(py_type, (np.floating, float)):
        return "DOUBLE"
    if issubclass(py_type, (bool, np.bool_)):
        return "BOOLEAN"
    if issubclass(py_type, (np.datetime64, pd.Timestamp, datetime)):
        return "TIMESTAMP"
    if issubclass(py_type, date):
        return "DATE"
    raise ValueError(f"Unsupported type {py_type}")
