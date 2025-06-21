# -*- coding: utf-8 -*-
# @Time    : 2024/11/12 13:49
# @Author  : YQ Tsui
# @File    : metadata_sql_cb.py
# @Purpose :

import pandas as pd

from .metadata_sql import MetadataSql
from .typedefs import EXCHANGE_LITERALS, Opt_T_SeqT, T_SeqT


class CBMetadataSql(MetadataSql):

    def read_latest_conversion_price(
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
            "cb_convert_price_history", fields, ["ticker", "exchange"], latest_by, filter_fields=filter_fields
        )
        return df.set_index(["ticker", "exchange"])

    def update_conversion_price(self, data: pd.DataFrame):
        """
        Updates the convert price metadata in the database.

        :param data: The data to update.
        :type data: pd.DataFrame
        """
        self._manager.insert("cb_convert_price_history", data, upsert=True)

    def read_bond_coupon(
        self, fields: T_SeqT[str], tickers: Opt_T_SeqT[str] = None, exchanges: Opt_T_SeqT[EXCHANGE_LITERALS] = None
    ) -> pd.DataFrame:
        """
        Reads the convert coupon metadata from the database.

        :param fields: The fields to query. available fields: ["pay_date", "coupon", "coupon_type", "period_start", "period_end", "remaining_principle", "principle_repayment"]
        :type fields: T_SeqT[str]
        :param tickers: The tickers to query, None for all.
        :type tickers: Opt_T_SeqT[str]
        :param exchanges: The exchanges to query, None for all.
        :type exchanges: Opt_T_SeqT[EXCHANGE_LITERALS]
        """
        filter_fields = {}
        if tickers is not None:
            filter_fields["ticker"] = tickers
        if exchanges is not None:
            filter_fields["exchange"] = exchanges

        df = self._manager.read_data("cb_coupon_schedule", query_fields=fields, filter_fields=filter_fields)
        return df.set_index(["ticker", "exchange"])

    def update_bond_coupon(self, data):
        """
        Updates the convert coupon metadata in the database.

        :param data: The data to update.
        :type data: pd.DataFrame
        """
        self._manager.insert("cb_coupon_schedule", data, upsert=True)

    def update_convert_bond_cashflow(self, data):
        """
        Updates the convert bond cashflow metadata in the database.

        :param data: The data to update.
        :type data: pd.DataFrame
        """
        self._manager.insert("cb_realized_cash_flow", data, upsert=True)

    def update_auxiliary(self, auxiliary_type: str, data: pd.DataFrame):
        """
        Updates the auxiliary data in the database.

        :param auxiliary_type: ["RangerBinary"]
        :param data: The data to update.
        :type data: pd.DataFrame
        """
        self._manager.insert("cb_auxiliary_" + auxiliary_type, data, upsert=True)
