from functools import partial
from typing import Literal, Sequence, Union

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

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

    def _add_unique_index(self, table_name: str, columns: Union[str, Sequence[str]]):
        if isinstance(columns, str):
            columns = [columns]
        index_name = f"uix_{table_name}_{'_'.join(columns)}"
        columns_str = ", ".join(columns)
        self.engine.execute(f"CREATE UNIQUE INDEX {index_name} ON {table_name} ({columns_str})")

    def insert(self, table_name: str, df: pd.DataFrame, upsert: bool = True):
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
            self._add_unique_index(table_name, df.index.names)
        return num_rows
