# -*- coding: utf-8 -*-
# @Time    : 2024/11/19 17:42
# @Author  : YQ Tsui
# @File    : metadata_fut.py
# @Purpose :

import pandas as pd

from .metadata_sql import MetadataSql
from .typedefs import EXCHANGE_LITERALS, Opt_T_SeqT, T_SeqT

class FutMetadataSql(MetadataSql):

    def get_all_underlying_codes(self) -> pd.Series:
        """
        Get all underlying codes
        """
        return self._manager.read_data("instruments_fut", ["underlying_code"], unique=True)
