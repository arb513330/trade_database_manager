# @Time    : 2024/4/22 17:48
# @Author  : YQ Tsui
# @File    : typedefs.py
# @Purpose :

from typing import Literal, TypeVar
from collections.abc import Sequence

T = TypeVar("T")
T_SeqT = T | Sequence[T]
Opt_T_SeqT = T | Sequence[T] | None

T_DictT = T | dict[str, T]
Opt_T_DictT = T | dict[str, T] | None

QUERYFIELD_TYPE = T_DictT[Literal["*"] | Sequence[str]]
FILTERFIELD_TYPE = Opt_T_DictT[dict[str, str | Sequence[str] | int | float | bool]]
