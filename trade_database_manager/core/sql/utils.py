# -*- coding: utf-8 -*-
# @Time    : 2024/10/31 21:07
# @Author  : YQ Tsui
# @File    : utils.py
# @Purpose : utility functions for sqlmanager

from sqlalchemy.types import TypeEngine, DOUBLE_PRECISION, Integer, String, Date, DateTime
import numpy as np
import pandas as pd
from datetime import datetime, date


def infer_sql_type(input_type):
    if isinstance(input_type, TypeEngine):
        return input_type
    if isinstance(input_type, (tuple, list)):
        input_type, *args = input_type
    else:
        args = ()
    if isinstance(input_type, str):
        return String(*args)
    if isinstance(input_type, (int, np.signedinteger)):
        return Integer()
    if isinstance(input_type, (np.floating, float)):
        return DOUBLE_PRECISION()
    if isinstance(input_type, (bool, np.bool_)):
        return Integer()
    if isinstance(input_type, (numpy.datetime64, pd.Timestamp, datetime)):
        return Date() if args and args[0] == True else DateTime()
    if isinstance(input_type, date):
        return Date()
    raise ValueError(f"Unsupported type {input_type}")