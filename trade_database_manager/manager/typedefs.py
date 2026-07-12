# @Time    : 2024/4/22 16:18
# @Author  : YQ Tsui
# @File    : typedefs.py
# @Purpose : Type hints for convenience

from typing import Literal, TypeVar
from collections.abc import Sequence
from enum import Enum

INST_TYPE_LITERALS = Literal["STK", "FUT", "OPT", "IDX", "ETF", "LOF", "FUND", "BOND", "CASH", "CRYPTO", "CB"]
EXCHANGE_LITERALS = Literal[
    "SSE",
    "SZSE",
    "HKEX",
    "CFFEX",
    "SHFE",
    "DCE",
    "CZCE",
    "SGX",
    "CBOT",
    "CME",
    "COMEX",
    "NYMEX",
    "ICE",
    "LME",
    "TOCOM",
    "JPX",
    "KRX",
    "ASX",
    "NSE",
    "BSE",
    "NSE",
    "BSE",
    "MCX",
    "MOEX",
    "TSE",
    "TWSE",
    "SET",
    "IDX",
    "CRYPTO",
    "SMART",
]

T = TypeVar("T")
T_SeqT = T | Sequence[T]
Opt_T_SeqT = T | Sequence[T] | None

T_DictT = T | dict[str, T]
Opt_T_DictT = T | dict[str, T] | None

class Interval(Enum):
    """Interval for time series data."""

    MIN01 = 60
    MIN05 = 300
    MIN15 = 900
    MIN30 = 1800
    HOUR01 = 3600
    HOUR04 = 14400
    DAY = 86400
    WEEK = 604800
    MONTH = 2592000
