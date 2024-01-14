from functools import partial
from typing import Literal, Sequence, Union

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
import re
from ...config import CONFIG


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
    def __init__(
        self,
    ):
        self.engine = create_engine(CONFIG["sqlconnstr"])

    def add_index(self, table_name: str, columns: Union[str, Sequence[str]], unique: bool = True):
        if isinstance(columns, str):
            columns = [columns]
        index_name = f"uix_{table_name}_{'_'.join(columns)}"
        columns_str = ", ".join(columns)
        unique_str = "UNIQUE" if unique else ""
        self.engine.execute(f"CREATE {unique_str} INDEX {index_name} ON {table_name} ({columns_str})")

    def insert(
        self,
        table_name: str,
        df: pd.DataFrame,
        upsert: bool = True,
        other_unique_index_columns: Sequence[str] = (),
        other_non_unique_index_columns: Sequence[str] = (),
    ):
        if_exists: Literal["replace", "append"] = "append"
        method = (
            partial(_insert_on_conflict_update, indexes=df.index.names)
            if upsert and self.engine.has_table(table_name)
            else None
        )
        new_table = not self.engine.has_table(table_name)
        num_rows = df.to_sql(
            table_name,
            self.engine,
            if_exists=if_exists,
            index=True,
            index_label=df.index.names,
            method=method,
        )
        if new_table:
            self.add_index(table_name, df.index.names)
            for column in other_unique_index_columns:
                self.add_index(table_name, column, unique=True)
            for column in other_non_unique_index_columns:
                self.add_index(table_name, column, unique=False)
        return num_rows

    def rename_column(self, table_name: str, old_column_name: str, new_column_name: str):
        # check if any index is referring to the column
        sql_code = f"SELECT indexname, indexdef FROM pg_indexes WHERE indexdef LIKE '%%(%%{old_column_name}%%)%%' and tablename = '{table_name}'"
        index_refering_column = dict(self.engine.execute(sql_code).fetchall())

        # drop indexes referring to the column
        for index_name in index_refering_column:
            self.engine.execute(f"DROP INDEX IF EXISTS {index_name}")

        # rename the column
        self.engine.execute(f"ALTER TABLE {table_name} RENAME COLUMN {old_column_name} TO {new_column_name}")

        # recreate the indexes
        for index_name, index_def in index_refering_column.items():
            new_index_name = index_name.replace(old_column_name, new_column_name)
            new_index_def = re.sub(rf"\(([^)]*?){old_column_name}([^)]*?)\)", rf"(\1{new_column_name}\2)", index_def)
            new_index_def = new_index_def.replace(index_name, new_index_name)
            self.engine.execute(new_index_def)

    def read_range_data(
        self, table_name: str, query_fields, start_time: pd.Timestamp, end_time: pd.Timestamp, filter_fields=None
    ):
        sql_code = f"SELECT {', '.join(query_fields)} FROM {table_name} WHERE end_time >= '{start_time}' AND start_time <= '{end_time}'"
        if filter_fields:
            sql_code += (
                f" AND {' AND '.join([f'{field} IN {tuple(filter_values)}' for field, filter_values in filter_fields])}"
            )

        # cannot use pandas.read_sql here as it discards timezone info
        res = self.engine.execute(sql_code)
        return pd.DataFrame(res.fetchall(), columns=res.keys())
