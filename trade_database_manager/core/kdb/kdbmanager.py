# -*- coding: utf-8 -*-
# @Time    : 2024/4/27 12:26
# @Author  : YQ Tsui
# @File    : kdbmanager.py
# @Purpose :
import pandas as pd
import pykx
from ...config import CONFIG


class KdbManager:

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.host = CONFIG["kdbhost"]
        self.port = CONFIG["kdbport"]
        # self.username = CONFIG["username"]
        # self.password = CONFIG["password"]
        self.username = ""
        self.password = ""

    def write(self, table_name: str, data: pd.DataFrame):
        """
        Writes data to the kdb database.

        :param table_name: The table name in the kdb database.
        :type table_name: str
        :param data: The data to be written.
        :type data: pd.DataFrame
        """
        with pykx.QConnection(self.host, self.port, username=self.username, password=self.password) as conn:
            conn(f"{{`:{table_name}/ set x}}", data)