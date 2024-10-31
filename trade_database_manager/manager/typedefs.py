# -*- coding: utf-8 -*-
# @Time    : 2024/4/22 16:18
# @Author  : YQ Tsui
# @File    : typedefs.py
# @Purpose : Type hints for convenience

from typing import Dict, Literal, Optional, Sequence, TypeVar, Union

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
T_SeqT = Union[T, Sequence[T]]
Opt_T_SeqT = Optional[Union[T, Sequence[T]]]

T_DictT = Union[T, Dict[str, T]]
Opt_T_DictT = Optional[Union[T, Dict[str, T]]]
