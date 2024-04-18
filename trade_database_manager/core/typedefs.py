# -*- coding: utf-8 -*-
# @Time    : 2024/4/22 17:48
# @Author  : YQ Tsui
# @File    : typedefs.py
# @Purpose :

from typing import Dict, Literal, Optional, Sequence, TypeVar, Union

T = TypeVar("T")
T_SeqT = Union[T, Sequence[T]]
Opt_T_SeqT = Optional[Union[T, Sequence[T]]]

T_DictT = Union[T, Dict[str, T]]
Opt_T_DictT = Optional[Union[T, Dict[str, T]]]

QUERYFIELD_TYPE = T_DictT[Union[Literal["*"], Sequence[str]]]
FILTERFIELD_TYPE = Opt_T_DictT[Dict[str, Union[str, Sequence[str], int, float, bool]]]
