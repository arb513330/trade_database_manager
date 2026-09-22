from trade_database_manager.core.sql.sqlmanager import SqlManager


def test_compute_in_field_batch_limit_respects_parameter_ceiling():
    values = [f"TICKER_{i}" for i in range(70000)]
    per_field_limit = SqlManager._compute_in_field_batch_limit({"ticker": values})
    assert per_field_limit == 65534


def test_compute_in_field_batch_limit_respects_large_value_size():
    values = ["X" * 200000 for _ in range(1000)]
    per_field_limit = SqlManager._compute_in_field_batch_limit({"ticker": values})
    assert per_field_limit < len(values)
    assert per_field_limit <= 335
