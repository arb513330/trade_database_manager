# -*- coding: utf-8 -*-
# @Time    : 2024/4/27 12:26
# @Author  : YQ Tsui
# @File    : kdbmanager.py
# @Purpose :
import os.path
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
        # print(self.host, self.port, self.username, self.password)

    def path_exists(self, path: str):
        """
        Checks if a path exists in the kdb database.

        :param path: The path to check.
        :type path: str
        """
        with pykx.QConnection(self.host, self.port, username=self.username, password=self.password) as conn:
            return bool(conn(f".path.exists `:{path}"))

    def create_folder(self, path: str):
        """
        Creates a folder in the kdb database.

        :param path: The path of the folder.
        :type path: str
        """
        with pykx.QConnection(self.host, self.port, username=self.username, password=self.password) as conn:
            conn(f'.path.mkdir "{path}"')

    def write(self, table_name: str, data: pd.DataFrame, path: str = "", splayed: bool = False):
        """
        Writes data to the kdb database.

        :param table_name: The table name in the kdb database.
        :type table_name: str
        :param data: The data to be written.
        :type data: pd.DataFrame
        :param path: The path of data
        :type path: str
        :param splayed: Whether the table is splayed.
        :type splayed: bool
        """

        real_path = os.path.join(path, table_name) if path else table_name
        if splayed:
            real_path += "/"
        with pykx.QConnection(self.host, self.port, username=self.username, password=self.password) as conn:
            conn(f"{{`:{real_path}/ set x}}", data)

    def write_partitioned(
        self, table_name: str, data: pd.DataFrame, path: str = "", partition_func=None, key_column=None
    ):
        """
        Writes data to the kdb database with partitioning.

        :param table_name: The table name in the kdb database.
        :type table_name: str
        :param data: The data to be written.
        :type data: pd.DataFrame
        :param path: The path of data
        :type path: str
        :param partition_func: calculate the partition bucket from datetime column
        :type partition_func: callable
        :param key_column: Column which is sorted to server as key.
        :type key_column: str
        """
        if isinstance(data.index, pd.DatetimeIndex):
            data.reset_index(inplace=True, names=["datetime"])
        assert "datetime" in data.columns, "datetime column not found"
        data.sort_values(by="datetime", inplace=True)
        if key_column is not None and key_column in data.columns:
            with pykx.QConnection(
                host=self.host, port=self.port, username=self.username, password=self.password
            ) as conn:
                for bucket, df in data.groupby(partition_func(data["datetime"])):
                    conn(
                        f"{{`{table_name} set x; .partable.createOrAppend[`:{path};{bucket};`{key_column};`{table_name}]}}",
                        df.reset_index(drop=True),
                    )
        else:
            with pykx.QConnection(
                host=self.host, port=self.port, username=self.username, password=self.password
            ) as conn:
                for bucket, df in data.groupby(by=partition_func(data["datetime"])):
                    conn(f"{{`{table_name} set x;.Q.dpt[`:{path};{bucket};`{table_name}]}}", df.reset_index(drop=True))

    def read_partitioned(
        self,
        table_name: str,
        path: str = "",
        fields=None,
        start_time=None,
        end_time=None,
        partition_func=None,
        other_conditions=None,
    ):
        """
        Reads data from the kdb database with partitioning.

        :param table_name: The table name in the kdb database.
        :type table_name: str
        :param path: The path of data
        :type path: str
        :param start_time: The start time of the data.
        :type start_time: pd.Timestamp
        :param end_time: The end time of the data.
        :type end_time: pd.Timestamp
        :param partition_func: calculate the partition bucket from datetime column
        :type partition_func: callable
        :param other_conditions: Other conditions for the query.
        :type other_conditions: str
        """
        time_format = "%Y.%m.%dD%H:%M:%S.%f"
        start_time_str = start_time.strftime(time_format) if start_time is not None else None
        end_time_str = end_time.strftime(time_format) if end_time is not None else None
        if isinstance(fields, (bytes, str)):
            fields = [fields]
        select_clause = (
            f"select from {table_name}" if fields is None else f"select {','.join(fields)} from {table_name}"
        )
        where_cond = ""
        if start_time_str is not None and end_time_str is not None:
            where_cond += f"datetime within ({start_time_str};{end_time_str})"
            if partition_func is not None:
                where_cond = (
                    f"int in {' '.join(str(x) for x in range(partition_func(start_time), partition_func(end_time) + 1))}"
                    + ","
                    + where_cond
                )
        elif start_time_str is not None:
            where_cond += f"datetime>={start_time_str}"
            if partition_func is not None:
                where_cond = f"int>={partition_func(start_time)}" + "," + where_cond
        elif end_time_str is not None:
            where_cond += f"datetime<={end_time_str}"
            if partition_func is not None:
                where_cond = f"int<= {partition_func(end_time)}" + "," + where_cond
        if other_conditions is not None:
            where_cond = other_conditions if where_cond == "" else where_cond + "," + other_conditions
        if where_cond:
            where_cond = " where " + where_cond
        final_query = select_clause + where_cond

        with pykx.QConnection(host=self.host, port=self.port, username=self.username, password=self.password) as conn:
            conn(
                "`currpath__ set .path.pwd[]"
            )  # save current path to currpath__ as following command will change the path
            try:
                conn(f"\\l {path}")  # load the path
                q_table = conn(final_query)
                return q_table.pd().set_index("datetime")
            finally:
                conn('system "cd ", currpath__')
