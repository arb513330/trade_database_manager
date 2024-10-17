# -*- coding: utf-8 -*-
# @Time    : 2024/4/19 16:44
# @Author  : YQ Tsui
# @File    : fields_data_type.py
# @Purpose :

from sqlalchemy import DOUBLE_PRECISION, Integer, String, Date

FIELD_DATA_TYPE_SQL = {
    "ticker": String(20),
    "name": String(20),
    "currency": String(6),
    "exchange": String(10),
    "timezone": String(30),
    "tick_size": DOUBLE_PRECISION(),
    "lot_size": DOUBLE_PRECISION(),
    "min_lots": DOUBLE_PRECISION(),
    "market_tplus": Integer(),
    "listed_date": Date(),
    "delisted_date": Date(),
    "country": String(6),
    # STK
    "sector": String(30),
    "industry": String(36),
    "board_type": String(200),
    # LOF & ETF
    "issuer": String(60),
    "current_mgr": String(60),
    "custodian": String(60),
    "issuer_country": String(6),
    "fund_type": String(20),
    "benchmark": String(60),
}
