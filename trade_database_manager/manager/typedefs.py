# @Time    : 2024/4/22 16:18
# @Author  : YQ Tsui
# @File    : typedefs.py
# @Purpose : Type hints for convenience

from typing import Literal, TypeVar
from collections.abc import Sequence

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
