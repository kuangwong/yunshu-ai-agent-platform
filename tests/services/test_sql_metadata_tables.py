"""SQL AST 表名提取（元数据权限前置逻辑）单测。"""

from app.services.sql_query_execution_service import extract_physical_table_refs_from_select_sql


def test_extract_tables_simple_join():
    err, refs = extract_physical_table_refs_from_select_sql(
        "SELECT * FROM a.Bb JOIN cc AS z ON 1", dialect="mysql"
    )
    assert err is None
    assert refs["bb"] == "Bb"
    assert refs["cc"] == "cc"


def test_extract_skips_cte_and_subquery_alias():
    sql = """
    WITH t AS (SELECT 1)
    SELECT * FROM t
    JOIN (SELECT id FROM orders o) AS sq ON 1
    JOIN customers c ON 1
    """
    err, refs = extract_physical_table_refs_from_select_sql(sql, dialect="mysql")
    assert err is None
    assert "t" not in refs
    assert "sq" not in refs
    assert "orders" in refs
    assert "customers" in refs


def test_extract_invalid_sql():
    err, refs = extract_physical_table_refs_from_select_sql("SELECT * FROM (", dialect="mysql")
    assert err is not None
    assert refs == {}
