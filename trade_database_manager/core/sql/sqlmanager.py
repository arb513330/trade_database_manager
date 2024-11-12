# -*- coding: utf-8 -*-
# @Time    : 2024/4/15 20:28
# @Author  : YQ Tsui
# @File    : sqlmanager.py
# @Purpose : Core SQL operations

import re
from collections.abc import Container
from functools import partial, reduce
from typing import Any, Literal, Sequence, Union, Callable

import pandas as pd
from sqlalchemy import Index, MetaData, Table, Column, create_engine, inspect, select, text, func, and_, Executable, PrimaryKeyConstraint
from sqlalchemy.types import TypeEngine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker, aliased

from .utils import infer_sql_type
from ...config import CONFIG
from ..typedefs import FILTERFIELD_TYPE, QUERYFIELD_TYPE


def _insert_on_conflict_update(table, conn, keys, data_iter, indexes):
    data = [dict(zip(keys, row)) for row in data_iter]
    stmt = insert(table.table).values(data)
    stmt = stmt.on_conflict_do_update(index_elements=indexes, set_={k: getattr(stmt.excluded, k) for k in keys})
    result = conn.execute(stmt)
    return result.rowcount


def _insert_on_conflict_nothing(table, conn, keys, data_iter):
    data = [dict(zip(keys, row)) for row in data_iter]
    stmt = insert(table.table).values(data).on_conflict_do_nothing(index_elements=keys)
    result = conn.execute(stmt)
    return result.rowcount


class SqlManager:
    """
    This class is used to manage SQL operations.

    :ivar sqlalchemy.engine.Engine engine: An instance of the SQLAlchemy Engine class for executing SQL operations.
    :ivar sqlalchemy.orm.Session _session: An instance of the SQLAlchemy Session class for executing SQL operations.
    :ivar sqlalchemy.engine.reflection.Inspector _inspector: An instance of the SQLAlchemy Inspector class for inspecting the database.
    """

    def __init__(self):
        self.engine = create_engine(CONFIG["sqlconnstr"])
        self._session = sessionmaker(bind=self.engine)()  # TODO: is it better to be a class attribute?
        self._inspector = None

    @property
    def inspector(self):
        if self._inspector is None:
            self._inspector = inspect(self.engine)
        return self._inspector

    def _execute(self, sql_executable: Union[str, Executable]) -> Any:
        if isinstance(sql_executable, str):
            sql_executable = text(sql_executable)
        with self.engine.begin() as conn:
            return conn.execute(sql_executable)

    def add_index(self, table_name: str, columns: Union[str, list[str]], unique: bool = True):
        """
        Adds an index to a table.

        :param table_name: The name of the table to add the index to.
        :type table_name: str
        :param columns: The column(s) to include in the index. It can be a single column name or a list of column names.
        :type columns: Union[str, list[str]]
        :param unique: Whether the index should enforce unique values. Defaults to True.
        :type unique: bool
        """
        if isinstance(columns, str):
            columns = [columns]

        index_name = f"uix_{table_name}_{'_'.join(columns)}"
        table_meta = MetaData()
        table = Table(table_name, table_meta, autoload_with=self.engine)
        columns = [getattr(table.c, colname) for colname in columns if colname in table.c.keys()]
        index = Index(index_name, *columns, unique=unique)
        index.create(bind=self.engine)

    def insert(
        self,
        table_name: str,
        df: pd.DataFrame,
        upsert: bool = True,
        other_unique_index_columns: Sequence[str] = (),
        other_non_unique_index_columns: Sequence[str] = (),
    ):
        """
        Inserts data into a table.

        :param table_name: The name of the table to insert data into.
        :type table_name: str
        :param df: The data to insert. It should be a DataFrame where the column names match the table columns.
        :type df: pd.DataFrame
        :param upsert: Whether to update the table if the data already exists. Defaults to True.
        :type upsert: bool
        :param other_unique_index_columns: Other columns to enforce unique values on. Defaults to an empty sequence.
        :type other_unique_index_columns: Sequence[str]
        :param other_non_unique_index_columns: Other columns to add non-unique indexes to. Defaults to an empty sequence.
        :type other_non_unique_index_columns: Sequence[str]
        :return: The number of rows inserted.
        :rtype: int
        """
        if_exists: Literal["replace", "append"] = "append"
        new_table = not self.inspector.has_table(table_name)
        method = partial(_insert_on_conflict_update, indexes=df.index.names) if upsert and not new_table else None
        num_rows = df.to_sql(
            table_name,
            self.engine,
            if_exists=if_exists,
            index=True,
            index_label=df.index.names,
            method=method,
        )
        if new_table:
            self.add_index(table_name, df.index.names, unique=True)
            for column in other_unique_index_columns:
                self.add_index(table_name, column, unique=True)
            for column in other_non_unique_index_columns:
                self.add_index(table_name, column, unique=False)
        return num_rows

    def table_exists(self, table_name: str) -> bool:
        """
        Checks if a table exists.

        :param table_name: The name of the table to check for.
        :type table_name: str
        :return: True if the table exists, False otherwise.
        :rtype: bool
        """
        return self.inspector.has_table(table_name)

    def create_table(
        self,
        table_name: str,
        table_columns: list[tuple[str, TypeEngine | type | tuple[type, tuple]]],
        unique_index_columns: Sequence[str] = (),
        primary_key: str|set[str] = set(),
    ):
        """
        Creates a table.

        :param table_name: The name of the table to create.
        :type table_name: str
        :param table_columns: The names of the columns to create with their data types. The keys are the column names and the values are the data types.
        :type table_columns: dict[str, Type]
        :param unique_index_columns: Columns to enforce unique values on. Defaults to an empty sequence.
        :type unique_index_columns: Sequence[str]
        :param primary_key: The primary key(s) of the table. Defaults to an empty set.
        :type primary_key: Union[str, set[str|tuple[str]]]

        """
        table_meta = MetaData()
        columns = [Column(name, infer_sql_type(col_type)) for name, col_type in table_columns]
        if isinstance(primary_key, str) and primary_key != "":
            columns.append(PrimaryKeyConstraint(primary_key))
        elif primary_key:
            columns.append(PrimaryKeyConstraint(*primary_key, name=f"pk_{table_name}"))

        table = Table(table_name, table_meta, *columns)
        table.create(self.engine)
        for column in unique_index_columns:
            self.add_index(table_name, column, unique=True)

    def insert_column(self, table_name: str, column_name: str, column_type: str):
        """
        Inserts a new column into a table.

        :param table_name: The name of the table to insert the column into.
        :type table_name: str
        :param column_name: The name of the new column.
        :type column_name: str
        :param column_type: The data type of the new column. It is a string representation of the data type in PostgreSQL.
        :type column_type: str
        """
        sql_code = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        self._execute(sql_code)

    def delete_column(self, table_name: str, column_name: str):
        """
        Deletes a column from a table.

        :param table_name: The name of the table to delete the column from.
        :type table_name: str
        :param column_name: The name of the column to delete.
        :type column_name: str
        """
        self._execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")

    def rename_column(self, table_name: str, old_column_name: str, new_column_name: str):
        """
        Renames a column in a table.

        :param table_name: The name of the table containing the column to rename.
        :type table_name: str
        :param old_column_name: The current name of the column.
        :type old_column_name: str
        :param new_column_name: The new name for the column.
        :type new_column_name: str
        """
        # check if any index is referring to the column
        sql_code = f"SELECT indexname, indexdef FROM pg_indexes WHERE indexdef LIKE '%%(%%{old_column_name}%%)%%' and tablename = '{table_name}'"
        index_refering_column = dict(self._execute(sql_code).fetchall())

        # drop indexes referring to the column
        for index_name in index_refering_column:
            self._execute(f"DROP INDEX IF EXISTS {index_name}")

        # rename the column
        self._execute(f"ALTER TABLE {table_name} RENAME COLUMN {old_column_name} TO {new_column_name}")

        # recreate the indexes
        for index_name, index_def in index_refering_column.items():
            new_index_name = index_name.replace(old_column_name, new_column_name)
            new_index_def = re.sub(rf"\(([^)]*?){old_column_name}([^)]*?)\)", rf"(\1{new_column_name}\2)", index_def)
            new_index_def = new_index_def.replace(index_name, new_index_name)
            self._execute(new_index_def)

    def read_range_data(
        self,
        table_name: str,
        query_fields="*",
        start_time: pd.Timestamp = None,
        end_time: pd.Timestamp = None,
        filter_fields=None,
    ):
        meta = MetaData()
        table = Table(table_name, meta, autoload_with=self.engine)

        if query_fields != "*":
            query_fields = [table.columns[field] for field in query_fields]
        else:
            query_fields = table

        stmt = select(*query_fields)

        conditions = []
        if start_time:
            conditions.append(getattr(table.c, "timestamp", table.c.end_time) >= start_time)
        if end_time:
            conditions.append(getattr(table.c, "timestamp", table.c.start_time) <= end_time)
        if filter_fields:
            conditions.extend(
                [
                    (
                        table.columns[field].in_(filter_values)
                        if isinstance(filter_values, Container) and not isinstance(filter_values, (str, bytes))
                        else table.columns[field] == filter_values
                    )
                    for field, filter_values in filter_fields.items()
                ]
            )
        stmt = stmt.where(and_(*conditions))
        res = self._execute(stmt)
        return pd.DataFrame(res.fetchall(), columns=res.keys())

    def read_data(self, table_name: str, query_fields: QUERYFIELD_TYPE = "*", filter_fields=None):
        """
        Reads data from a table.

        :param table_name: The name of the table to read data from.
        :type table_name: str
        :param query_fields: The fields to query. By default, it queries all fields. Defaults to "*".
        :type query_fields: QUERYFIELD_TYPE, optional
        :param filter_fields: Additional fields to filter by. The keys are the field names and the values are the filter values. Defaults to None.
        :type filter_fields: dict, optional
        :return: A DataFrame containing the queried data.
        :rtype: pd.DataFrame
        """

        meta = MetaData()
        table = Table(table_name, meta, autoload_with=self.engine)

        if query_fields != "*":
            query_fields = [table.columns[field] for field in query_fields]
            stmt = select(*query_fields)
        else:
            query_fields = table
            stmt = select(table)

        if filter_fields:
            conditions = [
                (
                    table.columns[field].in_(filter_values)
                    if isinstance(filter_values, Container) and not isinstance(filter_values, (str, bytes))
                    else table.columns[field] == filter_values
                )
                for field, filter_values in filter_fields.items()
            ]
            stmt = stmt.where(and_(*conditions))

        # cannot use pandas.read_sql here as it discards timezone info
        res = self._execute(stmt)
        return pd.DataFrame(res.fetchall(), columns=res.keys())

    def read_data_across_tables(
        self,
        table_names: Sequence[str],
        joined_columns: Sequence[str],
        query_fields: QUERYFIELD_TYPE = "*",
        filter_fields: FILTERFIELD_TYPE = None,
    ):
        """
        Reads data from multiple tables.

        :param table_names: The names of the tables to read data from.
        :type table_names: Sequence[str]
        :param joined_columns: The columns to join the tables on.
        :type joined_columns: Sequence[str]
        :param query_fields: The fields to query. By default, it queries all fields. Defaults to "*".
        :type query_fields: QUERYFIELD_TYPE, optional
        :param filter_fields: Additional fields to filter by. The keys are the field names and the values are the filter values. Defaults to None.
        :type filter_fields: FILTERFIELD_TYPE, optional
        :return: A DataFrame containing the queried data.
        :rtype: pd.DataFrame
        """
        meta = MetaData()
        tables = {table_name: Table(table_name, meta, autoload_with=self.engine) for table_name in table_names}

        joined_table = reduce(
            lambda x, y: x.join(y, and_(*[x.columns[col] == y.columns[col] for col in joined_columns])),
            tables.values(),
        )

        query_fields_rel = []
        if query_fields != "*":
            for table_name, colnames in query_fields.items():
                if isinstance(colnames, str):
                    query_fields_rel.append(tables[table_name].columns[colnames])
                else:
                    query_fields_rel.extend([tables[table_name].columns[colname] for colname in colnames])
        else:
            query_fields_rel = [text("*")]

        stmt = select(*query_fields_rel).select_from(joined_table)

        if filter_fields:
            conditions = []
            for table_name, colnames in filter_fields.items():
                table = tables[table_name]
                for field, filter_values in colnames.items():
                    conditions.append(
                        table.columns[field].in_(filter_values)
                        if isinstance(filter_values, Container) and not isinstance(filter_values, (str, bytes))
                        else table.columns[field] == filter_values
                    )

            stmt = stmt.where(and_(*conditions))

        # cannot use pandas.read_sql here as it discards timezone info
        res = self._execute(stmt)
        return pd.DataFrame(res.fetchall(), columns=res.keys())

    def _read_extremum_in_group(
        self,
        table_name: str,
        target_column: str | list[str],
        group_column: str | list[str],
        extremum_column: str,
        method: str | Callable,
        filter_fields=None,
    ):
        if isinstance(target_column, str):
            target_column = [target_column]
        if isinstance(group_column, str):
            group_column = [group_column]
        target_column_c = [Column(col) for col in target_column]
        group_column_c = [Column(col) for col in group_column]
        extremum_column_c = Column(extremum_column)

        table_meta = MetaData()

        full_table = (
            Table(table_name, table_meta, *target_column_c, *group_column_c)
            if extremum_column in target_column
            else Table(table_name, table_meta, *target_column_c, *group_column_c, extremum_column_c)
        )

        if isinstance(method, str):
            method = getattr(func, method)

        if filter_fields:
            conditions = [
                (
                    full_table.columns[field].in_(filter_values)
                    if isinstance(filter_values, Container) and not isinstance(filter_values, (str, bytes))
                    else full_table.columns[field] == filter_values
                )
                for field, filter_values in filter_fields.items()
            ]
            subquery = (
                self._session.query(*group_column_c, method(extremum_column_c).label("ColExtremum"))
                .where(and_(*conditions))
                .group_by(*group_column_c)
                .subquery()
            )
        else:
            subquery = (
                self._session.query(*group_column_c, method(extremum_column_c).label("ColExtremum"))
                .group_by(*group_column_c)
                .subquery()
            )

        t_full = aliased(full_table)
        t_sub = aliased(subquery)

        cols_res = [getattr(t_full.c, col) for col in group_column + target_column]

        query = self._session.query(*cols_res).join(
            t_sub,
            and_(
                getattr(t_full.c, extremum_column) == t_sub.c.ColExtremum,
                *[getattr(t_full.c, col) == getattr(t_sub.c, col) for col in group_column],
            ),
        )

        return pd.DataFrame(query.all(), columns=group_column + target_column)

    def read_max_in_group(
        self,
        table_name: str,
        target_column: str | list[str],
        group_column: str | list[str],
        extremum_column: str,
        filter_fields=None,
    ):
        return self._read_extremum_in_group(
            table_name, target_column, group_column, extremum_column, "max", filter_fields
        )

    def read_min_in_group(
        self,
        table_name: str,
        target_column: str | list[str],
        group_column: str | list[str],
        extremum_column: str,
        filter_fields=None,
    ):
        return self._read_extremum_in_group(
            table_name, target_column, group_column, extremum_column, "min", filter_fields
        )
