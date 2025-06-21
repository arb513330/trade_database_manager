# -*- coding: utf-8 -*-
# @Time    : 2024/4/19 16:44
# @Author  : YQ Tsui
# @File    : fields_data_type.py
# @Purpose :

from sqlalchemy import DOUBLE_PRECISION, Date, Integer, String, Text, DateTime, CHAR

FIELD_DATA_TYPE_SQL = {
    "ticker": String(20),
    "name": String(128),
    "trading_code": String(20),
    "inst_type": String(20),
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
    "state": String(36),
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
    # Convertible Bond (CB)
    "stock_ticker": String(20),
    "stock_exchange": String(10),
    "maturity_date": Date(),
    "issue_price": DOUBLE_PRECISION(),
    "total_issue_size": DOUBLE_PRECISION(),
    "par_value": DOUBLE_PRECISION(),
    "redeem_price": DOUBLE_PRECISION(),
    "conversion_start_date": Date(),
    "conversion_end_date": Date(),
    "callback_terms": Text(),
    "callback_type": String(20),
    "adjust_terms": Text(),
    "adjust_type": String(20),
    "putback_terms": Text(),
    "putback_type": String(20),
    "callback_level": DOUBLE_PRECISION(),
    # Futures (FUT)
    "contract_unit": String(20),
    "contract_multiplier": DOUBLE_PRECISION(),
    "expiry_time": DateTime(),
    "delivery_date": Date(),
    "settlement_method": String(20),
    "underlying_code": String(20),
    "underlying_exchange": String(10),
    "underlying_type": String(20),
    "margin_method": String(20),
    # Options (OPT), mainly similar to Futures
    "option_type": String(20),  # 'C' for Call, 'P' for Put
    "strike": DOUBLE_PRECISION(),
    "exercise_style": String(20),  # 'A' for American, 'E' for European
}

DATE_TIME_COLS = {"listed_date", "delisted_date", "maturity_date", "conversion_start_date", "conversion_end_date", "expiry_date", "delivery_date"}

BASE_COLUMNS = [("ticker", String(36)), ("exchange", String(10))]
