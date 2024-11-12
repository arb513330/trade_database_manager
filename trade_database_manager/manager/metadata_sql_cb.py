# -*- coding: utf-8 -*-
# @Time    : 2024/11/12 13:49
# @Author  : YQ Tsui
# @File    : metadata_sql_cb.py
# @Purpose :

from .metadata_sql import MetadataSql
import pandas as pd
from .typedefs import EXCHANGE_LITERALS, Opt_T_SeqT, T_SeqT


class CBMetadataSql(MetadataSql):

    def read_latest_convert_price(
        self,
        fields: T_SeqT[str] = ("conversion_price", "effective_date"),
        tickers: Opt_T_SeqT[str] = None,
        exchanges: Opt_T_SeqT[EXCHANGE_LITERALS] = None,
        latest_by: str = "announcement_date",
    ) -> pd.DataFrame:
        """
        Reads the convert price metadata from the database.

        :param fields: The fields to query.
        :type fields: T_SeqT[str]
        :param tickers: The tickers to query, None for all.
        :type tickers: Opt_T_SeqT[str]
        :param exchanges: The exchanges to query, None for all.
        :type exchanges: Opt_T_SeqT[EXCHANGE_LITERALS]
        :param latest_by: The field to use for latest by.
        :type latest_by: str
        """
        if tickers is None and exchanges is None:
            filter_fields = None
        else:
            filter_fields = {}
            if tickers is not None:
                filter_fields["ticker"] = tickers
            if exchanges is not None:
                filter_fields["exchange"] = exchanges

        df = self._manager.read_max_in_group(
            "cb_convert_price_history", fields, ["ticker", "exchange"], latest_by, filter_fields=filter_fields
        )
        return df.set_index(["ticker", "exchange"])
