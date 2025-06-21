# -*- coding: utf-8 -*-
# @Time    : 2024/11/19 17:42
# @Author  : YQ Tsui
# @File    : metadata_sql_fut.py
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

    def read_latest_daily_auxiliary_data(self, fields: T_SeqT[str] = "date",
        tickers: Opt_T_SeqT[str] = None,
        exchanges: Opt_T_SeqT[EXCHANGE_LITERALS] = None,
    ) -> pd.DataFrame:
        """
        Read latest daily auxiliary data
        """
        if isinstance(fields, str):
            fields = [fields]
        elif isinstance(fields, tuple):
            fields = list(fields)
        if tickers is None and exchanges is None:
            filter_fields = None
        else:
            filter_fields = {}
            if tickers is not None:
                filter_fields["ticker"] = tickers
            if exchanges is not None:
                filter_fields["exchange"] = exchanges

        df = self._manager.read_max_in_group(
            "fut_daily_auxiliary_data", fields, ["underlying_code", "exchange"], "date", filter_fields=filter_fields
        )
        return df.set_index(["underlying_code", "exchange"])