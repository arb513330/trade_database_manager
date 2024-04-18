# -*- coding: utf-8 -*-
# @Time    : 2024/4/15 20:28
# @Author  : YQ Tsui
# @File    : metadata_sql.py
# @Purpose : Instrument metadata stored in SQL database

from collections.abc import Container
from typing import Union

import pandas as pd

from ..core.sql.sqlmanager import SqlManager
from .typedefs import EXCHANGE_LITERALS, INST_TYPE_LITERALS, Opt_T_SeqT, T_DictT

COMMON_METADATA_COLUMNS = [
    "name",
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
    "STK": ["sector", "industry", "country", "state", "board_type"],
}


class MetadataSql:
    def __init__(self):
        self.manager = SqlManager()

    def update_instrument_metadata(self, data: Union[pd.DataFrame, list[dict], dict]):
        type_specific_columns = set(data.columns) - set(COMMON_METADATA_COLUMNS)
        if "inst_type" not in data.columns and bool(type_specific_columns):
            raise ValueError(
                f"Non-common columns found ({','.join(type_specific_columns)}) but inst_type column not provided."
            )
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            data = pd.DataFrame(data)
        data.set_index(["ticker", "exchange"], inplace=True)
        data_common = data[data.columns.intersection(COMMON_METADATA_COLUMNS)]

        self.manager.insert("instruments", data_common, upsert=True)
        if "inst_type" in data.columns:
            for inst_type, columns in TYPE_METADATA_COLUMNS.items():
                data_type_df = data.loc[data.inst_type == inst_type, data.columns.intersection(columns)]
                if not data_type_df.empty:
                    self.manager.insert(f"instruments_{inst_type.lower()}", data_type_df, upsert=True)

    def read_metadata(
        self,
        ticker: Opt_T_SeqT[str] = None,
        exchange: Opt_T_SeqT[EXCHANGE_LITERALS] = None,
        query_fields="*",
        filter_fields=None,
    ) -> T_DictT[pd.DataFrame]:
        filter_fields = filter_fields or {}
        if ticker is not None:
            filter_fields["ticker"] = ticker
        if exchange is not None:
            if not isinstance(exchange, str) and isinstance(exchange, Container):
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
        common_df = self.manager.read_data("instruments", query_fields=query_fields_common, filter_fields=filter_fields)
        if common_df.empty:
            return {}
        res = {}
        for inst_type, common_df_by_type in common_df.groupby("inst_type"):
            if all_fields_common:
                res[inst_type] = common_df_by_type
                continue
            query_fields_type = (
                ["ticker", "exchange"] + [f for f in query_fields if f in TYPE_METADATA_COLUMNS[inst_type]]
                if query_fields != "*"
                else "*"
            )
            filter_fields_type = {k: v for k, v in filter_fields.items() if k in TYPE_METADATA_COLUMNS[inst_type]}
            filter_fields_type["ticker"] = common_df_by_type["ticker"].to_list()
            filter_fields_type["exchange"] = common_df_by_type["exchange"].to_list()
            type_df = self.manager.read_data(
                f"instruments_{inst_type.lower()}", query_fields=query_fields_type, filter_fields=filter_fields_type
            )
            type_df = common_df_by_type.merge(type_df, on=["ticker", "exchange"], how="inner")
            res[inst_type] = type_df.set_index(["ticker", "exchange"])
        return res

    def read_metadata_for_insttype(
        self,
        inst_type: INST_TYPE_LITERALS,
        ticker: Opt_T_SeqT[str] = None,
        exchange: Opt_T_SeqT[EXCHANGE_LITERALS] = None,
        query_fields="*",
        filter_fields=None,
    ) -> pd.DataFrame:
        filter_fields = filter_fields or {}
        filter_fields["inst_type"] = inst_type
        if ticker is not None:
            filter_fields["ticker"] = ticker
        if exchange is not None:
            if not isinstance(exchange, str) and isinstance(exchange, Container):
                assert len(exchange) == len(ticker), "Exchange must be a single value or the same length as ticker."
            filter_fields["exchange"] = exchange

        if query_fields == "*":
            query_fields_cross = "*"
        else:
            query_fields_common = ["ticker", "exchange"] + [f for f in query_fields if f in COMMON_METADATA_COLUMNS]
            query_fields_type = ["ticker", "exchange"] + [
                f for f in query_fields if f in TYPE_METADATA_COLUMNS[inst_type]
            ]
            query_fields_cross = {
                "instruments": query_fields_common,
                f"instruments_{inst_type.lower()}": query_fields_type,
            }

        filter_fields_common = {
            k: v for k, v in filter_fields.items() if k in ["ticker", "exchange"] + COMMON_METADATA_COLUMNS
        }
        filter_fields_type = {
            k: v for k, v in filter_fields.items() if k in ["ticker", "exchange"] + TYPE_METADATA_COLUMNS[inst_type]
        }
        filter_fields_cross = {
            "instruments": filter_fields_common,
            f"instruments_{inst_type.lower()}": filter_fields_type,
        }
        df = self.manager.read_data_across_tables(
            ["instruments", f"instruments_{inst_type.lower()}"],
            joined_columns=["ticker", "exchange"],
            query_fields=query_fields_cross,
            filter_fields=filter_fields_cross,
        )
        if isinstance(df.columns, pd.Index):
            return df.loc[:, ~df.columns.duplicated()].set_index(["ticker", "exchange"])
        return df.set_index(["ticker", "exchange"])
