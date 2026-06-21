from app.services.ai.federated_sql_repair import (
    build_sql_repair_guidance,
    detect_sql_error,
    is_retryable_sql_error,
    normalize_sql_text,
)


def test_detect_sql_error_recognizes_tool_error():
    is_err, msg = detect_sql_error("[TOOL_ERROR] ORA-01861: literal does not match format string")
    assert is_err is True
    assert "ORA-01861" in msg


def test_is_retryable_sql_error_rejects_permission():
    assert is_retryable_sql_error("[Permission Denied] no access") is False


def test_is_retryable_sql_error_accepts_validation_failed():
    assert is_retryable_sql_error("[Validation Failed] unknown column 'foo'") is True


def test_build_sql_repair_guidance_includes_taxonomy_for_federated():
    guidance = build_sql_repair_guidance(
        "ORA-01861: literal does not match format string",
        "SELECT * FROM t WHERE d = TO_DATE('2026-05-01','YYYY-MM-DD')",
        for_federated_node=True,
    )
    assert "SQL Repair Taxonomy" in guidance
    assert "DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE" in guidance or "YYYY-MM-DD" in guidance


def test_normalize_sql_text_collapses_whitespace():
    assert normalize_sql_text("SELECT  1") == normalize_sql_text("select 1")
