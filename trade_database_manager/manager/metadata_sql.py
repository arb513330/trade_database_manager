# -*- coding: utf-8 -*-
# @Time    : 2024/4/15 20:28
# @Author  : YQ Tsui
# @File    : metadata_sql.py
# @Purpose : Instrument metadata stored in SQL database

from collections.abc import Container
from typing import Union, cast

import pandas as pd

from ..core.sql.sqlmanager import SqlManager
from .typedefs import EXCHANGE_LITERALS, INST_TYPE_LITERALS, Opt_T_SeqT, T_DictT
from .fields_data_type import FIELD_DATA_TYPE_SQL, String


COMMON_METADATA_COLUMNS = [
    "name",
    "trading_code",
    "inst_type",
    "currency",
    "timezone",
    "tick_size",
    "lot_size",
    "min_lots",
    "market_tplus",
    "listed_date",
    "delisted_date",
]
TYPE_METADATA_COLUMNS = {
    "STK": ["country", "state", "board_type", "issue_price"],
    "ETF": ["issuer", "current_mgr", "custodian", "issuer_country", "fund_type", "benchmark"],
    "LOF": ["issuer", "current_mgr", "custodian", "issuer_country", "fund_type", "benchmark"],
}


class MetadataSql:
    """
    This class is used to manage instrument metadata stored in an SQL database.

    This is a singleton class. Just call MetadataSql() to get the instance.
    """

    _instance = None
    _manager = None

    def __new__(cls):
        if not isinstance(cls._instance, cls):
            cls._instance = super(MetadataSql, cls).__new__(cls)
            cls._manager = SqlManager()
        return cls._instance

    def initialize(self, for_inst_types="all"):
        if for_inst_types == "all":
            for_inst_types = list(TYPE_METADATA_COLUMNS.keys())
        elif isinstance(for_inst_types, str):
            for_inst_types = [for_inst_types]
        columns_base = [("ticker", String(10)), ("exchange", String(10))]
        if not self._manager.table_exists("instruments"):
            columns = columns_base + [(col, FIELD_DATA_TYPE_SQL.get(col, String())) for col in COMMON_METADATA_COLUMNS]
            self._manager.create_table("instruments", columns, {"primary_key": ["ticker", "exchange"]})
        for inst_type in for_inst_types:
            if not self._manager.table_exists(f"instruments_{inst_type.lower()}"):
                columns = columns_base + [
                    (col, FIELD_DATA_TYPE_SQL.get(col, String())) for col in TYPE_METADATA_COLUMNS[inst_type]
                ]
                self._manager.create_table(
                    f"instruments_{inst_type.lower()}", columns, primary_key={"ticker", "exchange"}
                )

    def update_instrument_metadata(self, data: Union[pd.DataFrame, list[dict], dict]):
        """
        Updates the instrument metadata in the database.

        :param data: The data to be updated. It can be a DataFrame, a list of dictionaries, or a single dictionary.
        :type data: Union[pd.DataFrame, list[dict], dict]
        """
        type_specific_columns = set(data.columns) - set(COMMON_METADATA_COLUMNS)
        if "inst_type" not in data.columns and bool(type_specific_columns):
            raise ValueError(
                f"Non-common columns found ({','.join(type_specific_columns)}) but inst_type column not provided."
            )
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            data = pd.DataFrame(data)
        if "ticker" in data.columns and "exchange" in data.columns:
            data.set_index(["ticker", "exchange"], inplace=True)
        else:
            assert set(data.index.names) == {"ticker", "exchange"}, "Index names must be 'ticker' and 'exchange'."
        data_common = data[data.columns.intersection(COMMON_METADATA_COLUMNS)]

        self._manager.insert("instruments", data_common, upsert=True)
        if "inst_type" in data.columns:
            g = data.groupby("inst_type", group_keys=False)
            for inst_type, data_type_df in g:
                if not data_type_df.empty and inst_type in TYPE_METADATA_COLUMNS:
                    columns = data_type_df.columns.intersection(TYPE_METADATA_COLUMNS[inst_type])
                    if not columns.empty:
                        self._manager.insert(f"instruments_{inst_type.lower()}", data_type_df[columns], upsert=True)

    def _convert_datetime_columns(self, data: pd.DataFrame):
        for col in ["listed_date", "delisted_date"]:
            if col in data.columns:
                data[col] = data[col].apply(pd.to_datetime)

    def read_metadata(
        self,
        ticker: Opt_T_SeqT[str] = None,
        exchange: Opt_T_SeqT[EXCHANGE_LITERALS] = None,
        query_fields="*",
        filter_fields=None,
    ) -> T_DictT[pd.DataFrame]:
        """
        Reads metadata from the database based on the provided filters.

        :param ticker: The ticker(s) to filter by. It can be a single ticker or a sequence of tickers. Defaults to None.
        :type ticker: Opt_T_SeqT[str], optional
        :param exchange: The exchange(s) to filter by. It can be a single exchange or a sequence of exchanges. Defaults to None.
        :type exchange: Opt_T_SeqT[EXCHANGE_LITERALS], optional
        :param query_fields: The fields to query. By default, it queries all fields. Defaults to "*".
        :type query_fields: str, optional
        :param filter_fields: Additional fields to filter by. The keys are the field names and the values are the filter values. Defaults to None.
        :type filter_fields: dict, optional
        :return: A dictionary of DataFrames containing the queried metadata.
        :rtype: T_DictT[pd.DataFrame]
        """
        filter_fields = filter_fields or {}
        if ticker is not None:
            filter_fields["ticker"] = ticker
        if exchange is not None:
            if not isinstance(exchange, str) and isinstance(exchange, Container) and ticker is not None:
                assert len(exchange) == len(ticker), "Exchange must be a single value or the same length as ticker."
            filter_fields["exchange"] = exchange
        query_fields_common = (
            ["ticker", "exchange"] + [f for f in query_fields if f in COMMON_METADATA_COLUMNS]
            if query_fields != "*"
            else "*"
        )
        filter_fields_common = {
            k: v for k, v in filter_fields.items() if k in ["ticker", "exchange"] + COMMON_METADATA_COLUMNS
        }
        all_fields_common = (
            len(query_fields_common) == len(query_fields)
            and len(filter_fields_common) == len(filter_fields)
            and query_fields != "*"
        )
        if not (query_fields == "*" or "inst_type" in query_fields):
            query_fields_common.append("inst_type")
        common_df = self._manager.read_data(
            "instruments", query_fields=query_fields_common, filter_fields=filter_fields
        )
        self._convert_datetime_columns(common_df)
        if common_df.empty:
            return {}
        res = {}
        for inst_type, common_df_by_type in common_df.groupby("inst_type"):
            inst_type = cast(INST_TYPE_LITERALS, inst_type)
            if all_fields_common or inst_type not in TYPE_METADATA_COLUMNS:
                res[inst_type] = (
                    common_df_by_type.set_index(["ticker", "exchange"])
                    if len(common_df_by_type.columns) > 2
                    else common_df_by_type
                )
                continue
            query_fields_type = (
                ["ticker", "exchange"] + [f for f in query_fields if f in TYPE_METADATA_COLUMNS[inst_type]]
                if query_fields != "*"
                else "*"
            )
            filter_fields_type = {k: v for k, v in filter_fields.items() if k in TYPE_METADATA_COLUMNS[inst_type]}
            filter_fields_type["ticker"] = common_df_by_type["ticker"].to_list()
            filter_fields_type["exchange"] = common_df_by_type["exchange"].to_list()
            type_df = self._manager.read_data(
                f"instruments_{inst_type.lower()}", query_fields=query_fields_type, filter_fields=filter_fields_type
            )
            type_df = common_df_by_type.merge(type_df, on=["ticker", "exchange"], how="inner")
            res[inst_type] = type_df.set_index(["ticker", "exchange"]) if len(type_df.columns) > 2 else type_df
        return res

    def read_metadata_for_insttype(
        self,
        inst_type: INST_TYPE_LITERALS,
        ticker: Opt_T_SeqT[str] = None,
        exchange: Opt_T_SeqT[EXCHANGE_LITERALS] = None,
        query_fields="*",
        filter_fields=None,
    ) -> pd.DataFrame:
        """
        Reads metadata for a specific instrument type from the database based on the provided filters.

        :param inst_type: The instrument type to filter by.
        :type inst_type: INST_TYPE_LITERALS
        :param ticker: The ticker(s) to filter by. It can be a single ticker or a sequence of tickers. Defaults to None.
        :type ticker: Opt_T_SeqT[str], optional
        :param exchange: The exchange(s) to filter by. It can be a single exchange or a sequence of exchanges. Defaults to None.
        :type exchange: Opt_T_SeqT[EXCHANGE_LITERALS], optional
        :param query_fields: The fields to query. By default, it queries all fields. Defaults to "*".
        :type query_fields: str, optional
        :param filter_fields: Additional fields to filter by. The keys are the field names and the values are the filter values. Defaults to None.
        :type filter_fields: dict, optional
        :return: A DataFrame containing the queried metadata for the specified instrument type.
        :rtype: pd.DataFrame
        """
        filter_fields = filter_fields or {}
        filter_fields["inst_type"] = inst_type
        if ticker is not None:
            filter_fields["ticker"] = ticker
        if exchange is not None:
            if not isinstance(exchange, str) and isinstance(exchange, Container) and ticker is not None:
                assert len(exchange) == len(ticker), "Exchange must be a single value or the same length as ticker."
            filter_fields["exchange"] = exchange

        if query_fields == "*":
            query_fields_cross = "*"
        else:
            query_fields_common = ["ticker", "exchange"] + [f for f in query_fields if f in COMMON_METADATA_COLUMNS]
            query_fields_type = [f for f in query_fields if f in TYPE_METADATA_COLUMNS.get(inst_type, [])]
            query_fields_cross = {
                "instruments": query_fields_common,
                f"instruments_{inst_type.lower()}": query_fields_type,
            }

        filter_fields_common = {
            k: v for k, v in filter_fields.items() if k in ["ticker", "exchange"] + COMMON_METADATA_COLUMNS
        }
        filter_fields_type = {k: v for k, v in filter_fields.items() if k in TYPE_METADATA_COLUMNS.get(inst_type, [])}

        if (query_fields != "*" or inst_type not in TYPE_METADATA_COLUMNS) and not bool(query_fields_type) and not bool(filter_fields_type):
            df = self._manager.read_data(
                "instruments", query_fields=query_fields_common, filter_fields=filter_fields_common
            )
        else:
            filter_fields_cross = {
                "instruments": filter_fields_common,
                f"instruments_{inst_type.lower()}": filter_fields_type,
            }
            df = self._manager.read_data_across_tables(
                ["instruments", f"instruments_{inst_type.lower()}"],
                joined_columns=["ticker", "exchange"],
                query_fields=query_fields_cross,
                filter_fields=filter_fields_cross,
            )

        if isinstance(df.columns, pd.Index):
            df = df.loc[:, ~df.columns.duplicated()]
        if len(df.columns) > 2:
            df.set_index(["ticker", "exchange"], inplace=True)
        self._convert_datetime_columns(df)
        return df
