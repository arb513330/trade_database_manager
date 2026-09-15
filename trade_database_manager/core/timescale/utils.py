# @Time    : 2026/9/14
# @Author  : YQ Tsui
# @File    : utils.py
# @Purpose : TimescaleDB type inference and interval helpers

import re
from datetime import date, datetime

import numpy as np
import pandas as pd


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


def infer_timescale_type(py_type) -> str:
    """Infer a PostgreSQL/TimescaleDB column type from a Python type."""
    if py_type is None:
        raise ValueError(f"Unsupported type {py_type}")
    if issubclass(py_type, (bool, np.bool_)):
        return "BOOLEAN"
    if issubclass(py_type, (int, np.integer)):
        return "BIGINT"
    if issubclass(py_type, (float, np.floating)):
        return "DOUBLE PRECISION"
    if issubclass(py_type, str):
        return "TEXT"
    if issubclass(py_type, (np.datetime64, pd.Timestamp, datetime)):
        return "TIMESTAMP WITHOUT TIME ZONE"
    if issubclass(py_type, date):
        return "DATE"
    raise ValueError(f"Unsupported type {py_type}")


def translate_agg_func(func: str) -> str:
    """Map a QuestDB/IoTDB-style aggregate name to a PostgreSQL aggregate name."""
    upper = func.strip().upper()
    if upper in _KNOWN_AGG_VALUES:
        return upper
    mapped = AGG_FUNC_MAP.get(func.strip().lower())
    if mapped is None:
        raise ValueError(f"Unsupported aggregate function: {func}")
    return mapped


_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]+)\s*$")
_INTERVAL_UNITS = {
    "s": "second",
    "sec": "second",
    "second": "second",
    "seconds": "second",
    "m": "minute",
    "min": "minute",
    "minute": "minute",
    "minutes": "minute",
    "h": "hour",
    "hr": "hour",
    "hour": "hour",
    "hours": "hour",
    "d": "day",
    "day": "day",
    "days": "day",
    "w": "week",
    "week": "week",
    "weeks": "week",
    "mo": "month",
    "mon": "month",
    "month": "month",
    "months": "month",
    "y": "year",
    "yr": "year",
    "year": "year",
    "years": "year",
}


def normalize_timescale_interval(interval: str) -> str:
    """Normalize compact interval strings such as '1m' to PostgreSQL interval text."""
    value = interval.strip().lower()
    match = _INTERVAL_RE.match(value)
    if not match:
        raise ValueError(f"Unsupported interval: {interval!r}")
    number, unit = match.groups()
    normalized_unit = _INTERVAL_UNITS.get(unit)
    if normalized_unit is None:
        raise ValueError(f"Unsupported interval unit: {unit!r}")
    return f"{int(number)} {normalized_unit}"
