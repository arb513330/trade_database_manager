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

    def read_dominant_contracts(self, fields: T_SeqT[str], underlyings: T_SeqT[str] = None) -> pd.DataFrame:
        """
        Read daily auxiliary data

        Parameters
        ----------
        fields : T_SeqT[str]
            The fields to read, can be a single field or a list of fields.
        underlyings : T_SeqT[str], optional
            The underlying codes to filter by, if None, all underlyings are included.

        Returns
        -------
        pd.DataFrame
            A DataFrame containing the daily auxiliary data, indexed by underlying_code, exchange, and date.
        """
        if isinstance(fields, str):
            fields = [fields]
        elif isinstance(fields, tuple):
            fields = list(fields)

        fields = list(set(fields) | {"date", "underlying_code", "exchange"})

        df = self._manager.read_data(
            "fut_dominant_contracts",
            fields,
            filter_fields={"underlying_code": underlyings} if underlyings is not None else None,
        )
        return df.set_index(["underlying_code", "exchange", "date"])

    def read_latest_dominant_contracts(
        self,
        fields: T_SeqT[str] = "date",
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
            "fut_dominant_contracts", fields, ["underlying_code", "exchange"], "date", filter_fields=filter_fields
        )
        return df.set_index(["underlying_code", "exchange"])
