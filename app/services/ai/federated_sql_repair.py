"""联邦查询（FederatedQueryExecutor）专用的 SQL 错误检测与局部 repair 指引。

仅由 app.services.ai.executors.federated_executor 引用；
单源 ChatBI 仍使用 DataAgentRunner 内独立实现，与本模块无依赖。
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Tuple

from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.time_anchor import TIME_RANGE_GATE_PREFIX, build_data_query_time_anchor_block

SCHEMA_GATE_PREFIX = "[SCHEMA_GATE]"
SQL_PLAN_GATE_PREFIX = "[SQL_PLAN_GATE]"


def normalize_sql_text(sql: str) -> str:
    return " ".join(str(sql or "").strip().lower().split())


def is_cross_dataset_scope_sql_error(message: Any) -> bool:
    text = str(message or "")
    if not text.strip():
        return False
    return (
        "不属于当前指定的数据集" in text
        or "普通 execute_sql_query 严禁跨数据集" in text
    )


def is_schema_reference_sql_error(message: str) -> bool:
    err = str(message or "").lower()
    if not err.strip():
        return False
    patterns = (
        r"unknown column",
        r"unknown table",
        r"invalid column",
        r"invalid field",
        r"bad field",
        r"no such column",
        r"no such table",
        r"column .+ does not exist",
        r"column not found",
        r"undefined column",
        r"invalid identifier",
        r"unresolved column",
        r"table .+ doesn't exist",
        r"table .+ does not exist",
    )
    return any(re.search(pattern, err) for pattern in patterns)


def is_date_format_sql_error(message: str) -> bool:
    text = str(message or "").lower()
    if not text.strip():
        return False
    patterns = (
        "ora-01861",
        "ora-01830",
        "literal does not match format string",
        "date format",
        "datetime format",
        "cannot parse datetime",
        "cannot parse date",
    )
    return any(pattern in text for pattern in patterns)


def extract_invalid_sql_identifiers(message: str) -> list[str]:
    text = str(message or "")
    if not text.strip():
        return []
    candidates: list[str] = []
    patterns = (
        r"ORA-\d+:\s*(?:\"[^\"]+\"\.)?\"([^\"]+)\"\s*:\s*invalid identifier",
        r"unknown column\s+['\"]([^'\"]+)['\"]",
        r"no such column:\s*([A-Za-z_][A-Za-z0-9_.$]*)",
        r"column\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?\s+does not exist",
        r"invalid identifier\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?",
        r"unresolved column\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?",
    )
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text, flags=re.IGNORECASE))

    identifiers: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, tuple):
            candidate = next((part for part in candidate if part), "")
        value = str(candidate or "").strip().strip('"').strip("'")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        identifiers.append(value)
        if len(identifiers) >= 8:
            break
    return identifiers


def invalid_identifier_repair_hint(message: str) -> str:
    identifiers = extract_invalid_sql_identifiers(message)
    if not identifiers:
        return ""
    return (
        "\n\n【无效标识符定位】本次数据库明确报出的无效字段/标识符："
        f"{', '.join(identifiers)}。请在 Schema 返回的物理列名中逐项核对；"
        "若 schema 未列出这些字段，必须删除或替换为真实物理列，"
        "并同步修正 SELECT、JOIN、WHERE、GROUP BY、ORDER BY 中所有引用，"
        "不得继续使用这些字段名。"
    )


def cross_dataset_scope_repair_hint(message: str) -> str:
    if not is_cross_dataset_scope_sql_error(message):
        return ""
    return (
        "\n\n【跨数据集 SQL 修正要求】单数据集 SQL 中禁止引用其他数据集表。"
        "联邦查询应分别在各自 dataset 内生成子查询，再由内存关联阶段 join。"
    )


def sql_repair_taxonomy_hint(message: str) -> str:
    text = str(message or "")
    lower = text.lower()
    if is_cross_dataset_scope_sql_error(text):
        category = "cross_dataset_scope"
        focus = "移除单数据集 SQL 中的跨数据集表引用，改走联邦子查询 + 内存关联"
    elif is_date_format_sql_error(text):
        category = "date_format"
        focus = "核对日期字段类型、日期字面量格式、TO_DATE/TO_CHAR 或时间边界表达式"
    elif is_schema_reference_sql_error(text):
        category = "invalid_identifier"
        focus = "核对字段名、表名或别名引用"
    elif "not a group by" in lower or "group by expression" in lower or "ora-00979" in lower:
        category = "group_by_mismatch"
        focus = "核对 SELECT 中非聚合字段是否全部进入 GROUP BY，或改为聚合表达式"
    elif "join" in lower and ("cartesian" in lower or "missing" in lower or "condition" in lower):
        category = "join_condition_missing"
        focus = "补齐 JOIN ON 条件，并确认左右表关联键来自 Schema"
    elif "permission" in lower or "unauthorized" in lower or "access denied" in lower:
        category = "permission_denied"
        focus = "不要改写 SQL 绕过权限，应如实说明权限不足或请求授权"
    elif "syntax" in lower or "unexpected token" in lower or "invalid expression" in lower:
        category = "syntax_error"
        focus = "修正数据库方言语法、分页写法、函数名和括号结构"
    else:
        category = "sql_execution_error"
        focus = "根据数据库错误信息最小化修改 SQL，禁止无依据更换业务口径"
    return f"\n\n【SQL Repair Taxonomy】错误分类：{category}\n修复重点：{focus}。"


def _try_parse_json_output(tool_output: Any) -> Any:
    if isinstance(tool_output, (dict, list)):
        return tool_output
    text = str(tool_output or "").strip()
    if not text:
        return tool_output
    try:
        return json.loads(text)
    except Exception:
        if len(text) < 5000:
            try:
                return ast.literal_eval(text)
            except Exception:
                pass
    return tool_output


def _extract_result_row_lists(parsed: Any, depth: int = 0) -> list[list[Any]]:
    if depth > 4:
        return []
    if isinstance(parsed, list):
        return [parsed]
    if not isinstance(parsed, dict):
        return []
    row_lists: list[list[Any]] = []
    for key, value in parsed.items():
        if str(key) not in {"items", "rows", "data", "list", "result", "records"}:
            continue
        if isinstance(value, list):
            row_lists.append(value)
        elif isinstance(value, dict):
            row_lists.extend(_extract_result_row_lists(value, depth + 1))
    return row_lists


def is_structured_sql_result(parsed: Any) -> bool:
    if isinstance(parsed, list):
        return True
    if not isinstance(parsed, dict):
        return False
    if any(key in parsed for key in ("columns", "items", "rows", "data", "records")):
        return True
    return bool(_extract_result_row_lists(parsed))


def detect_sql_error(output: Any) -> Tuple[bool, str]:
    """检测 tool/SQL 执行结果是否应视为错误并进入联邦 repair。"""
    text = str(output or "")
    if not text.strip():
        return False, ""

    error_prefixes = (
        "[TOOL_ERROR]",
        "[Validation Failed]",
        "[Permission Denied]",
        "[Security Error]",
        "[Performance Blocked]",
        SCHEMA_GATE_PREFIX,
        SQL_PLAN_GATE_PREFIX,
        "Error: Dataset",
    )
    if any(text.startswith(prefix) for prefix in error_prefixes):
        return True, text[:1000]

    parsed = _try_parse_json_output(output)
    if is_structured_sql_result(parsed):
        return False, ""

    error_patterns = [
        r"unknown column",
        r"unknown table",
        r"syntax error",
        r"sql syntax",
        r"access denied",
        r"permission denied",
        r"unauthorized",
        r"SQL Syntax Error",
        r"SQL Validation Failed",
        r"\bORA-\d{3,5}\b",
        r"invalid identifier",
        r"invalid number",
        r"no such (?:column|table)",
        r"does not exist",
        r"not a group by",
        r"cannot parse",
        r"literal does not match",
        r"\btimed?\s*out\b",
        r"\btimeout\b",
        r"lock wait",
        r"connection (?:refused|reset|closed|error)",
        r"division by zero",
        r"\bCode:\s*\d+\.\s*DB::Exception",
        r"元数据中未找到指定的数据集",
        r"内存联邦 SQL",
        TIME_RANGE_GATE_PREFIX.lower(),
        r"相对时间",
        r"时间锚点",
        r"时间范围与用户相对时间",
    ]
    if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in error_patterns):
        return True, text[:1000]
    return False, ""


def is_non_retryable_permission_error(error: Any) -> bool:
    text = str(error or "")
    if not text.strip():
        return False
    lower = text.lower()
    markers = (
        "permission denied",
        "unauthorized",
        "access denied",
        "无权访问",
        "未提供有效的用户身份",
        "权限不足",
        "[security error]",
        "[permission denied]",
    )
    return any(marker in lower for marker in markers)


def is_retryable_sql_error(error: Any) -> bool:
    """联邦局部 SQL repair 是否应继续重试该节点。"""
    text = str(error or "")
    if not text.strip():
        return False
    if is_non_retryable_permission_error(text):
        return False
    lower = text.lower()
    non_retryable_markers = (
        "禁止外部访问",
        "外部访问",
        "只允许单条 select",
        "只允许 select/with",
        "只允许 select",
    )
    if any(marker in lower for marker in non_retryable_markers):
        return False
    is_err, _ = detect_sql_error(text)
    if is_err:
        return True
    if is_date_format_sql_error(text) or is_schema_reference_sql_error(text):
        return True
    return False


def build_sql_repair_guidance(
    error_text: str,
    failed_sql: str,
    *,
    repeat_blocked: bool = False,
    for_federated_node: bool = False,
) -> str:
    """构建联邦子查询 / memory_join 局部 repair 的修正指引。"""
    error_text = str(error_text or "").strip()
    failed_sql = str(failed_sql or "").strip()
    action = (
        "请只修正下方失败 SQL，并按输出格式返回修正后的 SQL。"
        if for_federated_node
        else "请基于 Schema 修正失败 SQL，并重新输出完整 `<multi_dataset_plan>` XML。"
    )
    repair = (
        "【SQL 修正要求】上一轮 SQL 执行失败。"
        f"错误信息：{error_text[:800]}\n"
        f"失败 SQL：\n{failed_sql[:4000]}\n"
        f"{action}"
        "禁止原样重复提交与上次完全相同的失败 SQL。"
    )
    repair += sql_repair_taxonomy_hint(error_text)
    if repeat_blocked:
        repair += (
            "\n\n【禁止重复 SQL】上一轮已提交相同失败 SQL 并被拦截。"
            "必须修改至少一处：列名、表名、JOIN、WHERE、时间范围或聚合逻辑。"
        )
    repair += invalid_identifier_repair_hint(error_text)
    repair += cross_dataset_scope_repair_hint(error_text)
    if is_schema_reference_sql_error(error_text):
        repair += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
    if is_date_format_sql_error(error_text):
        repair += f"\n\n{DataQueryPrompts.DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE}"
    err_lower = error_text.lower()
    if TIME_RANGE_GATE_PREFIX in error_text or "相对时间" in err_lower or "时间锚点" in error_text:
        repair += (
            f"\n\n{build_data_query_time_anchor_block()}\n\n"
            f"{DataQueryPrompts.TIME_RANGE_ANOMALY_REPAIR_GUIDE}"
        )
    if "invalid expression" in err_lower or "unexpected token" in err_lower:
        repair += f"\n\n{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}"
    return repair
