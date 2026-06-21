from __future__ import annotations

import json
import logging
import re
import uuid
import html
import xml.etree.ElementTree as ET
from typing import Any, AsyncGenerator, Dict, List, Optional
import pandas as pd
import duckdb

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.permission_service import PermissionService
from app.services.metadata_service import MetadataService
from app.services.sql_query_execution_service import execute_sql_query_core
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.time_anchor import (
    TIME_RANGE_GATE_PREFIX,
    build_data_query_time_anchor_block,
    build_time_range_gate_message,
    detect_time_range_mismatch,
)

logger = logging.getLogger(__name__)

MAX_FEDERATED_ROWS = 1000
# 子查询注册进 DuckDB 时的行上限。必须显著大于最终 join 输出上限，
# 否则「先截断子查询再 join/聚合」会丢行导致关联缺失、汇总失真。
MAX_FEDERATED_SUBQUERY_ROWS = 50000
MAX_FEDERATED_PLAN_REPAIR_ROUNDS = 4


def make_markdown_table(columns: list, rows: list) -> str:
    if not columns or not rows:
        return "无结果数据。"
    headers = [c["name"] for c in columns]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        row_str = []
        for val in r:
            s = str(val).replace("\n", " ") if val is not None else ""
            row_str.append(s)
        lines.append("| " + " | ".join(row_str) + " |")
    return "\n".join(lines)


class FederatedQueryExecutor:
    """跨数据集 DuckDB 联邦查询执行器"""

    def __init__(
        self,
        agent_runner: Any,
        schema_output: str,
        datasets: list[str],
    ):
        self.agent_runner = agent_runner
        self.schema_output = schema_output
        self.datasets = datasets
        self.user_info = agent_runner.user_info
        self.trace_buffer = agent_runner.trace_buffer

    async def execute(
        self,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
        *,
        _repair_attempt: int = 0,
        _repair_context: Optional[Dict[str, Any]] = None,
        _original_user_question: Optional[str] = None,
        _subquery_cache: Optional[Dict[str, str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext

        original_user_question = _original_user_question or user_question
        # 跨 repair 轮复用已成功的子查询结果，避免每轮重跑全部子查询造成的重复 DB 负载。
        # key = (dataset_name, 归一化 SQL)，仅命中完全相同的成功子查询才复用。
        if _subquery_cache is None:
            _subquery_cache = {}
        # 记录本次执行中发生的「降级留空 / 行截断」情况，最终注入 synthesis，避免把不完整结果当完整结论解读。
        degraded_datasets: list[str] = []
        truncated_notes: list[str] = []
        
        async with TraceSpanContext(
            trace_buffer=self.trace_buffer,
            event_type="agent_execution",
            span_name="FederatedQueryExecutor",
        ) as span:
            
            # 1. 计划生成日志
            plan_log_id = f"fed_plan_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": plan_log_id,
                "title": "生成跨源联邦查询计划",
                "details": "正在分析跨数据集元数据，并编排分布式子查询计划...",
                "status": "pending",
            }
            
            plan_output = ""
            try:
                llm = await AgentConfigProvider.get_configured_llm(
                    streaming=False,
                    config=getattr(self.agent_runner, "config", None),
                )
                chat_client = chat_client_from_handle(llm)
                
                # 从 schema 中提取数据集与物理数据库类型的映射，用于方言约束
                dataset_dialect_map = self._extract_dialect_map(self.schema_output)
                plan_prompt = DataQueryPrompts.build_federated_plan_prompt(
                    self.schema_output,
                    original_user_question,
                    dataset_dialect_map=dataset_dialect_map,
                )
                if _repair_context:
                    plan_prompt = self._build_federated_plan_repair_prompt(
                        plan_prompt,
                        _repair_context,
                        _repair_attempt,
                    )
                plan_messages = system_user_prompt_messages(plan_prompt, user_prompt=original_user_question)
                
                plan_output = await chat_client.generate_text(plan_messages)
                plan_output = plan_output.strip()
                
                # 解析 XML 执行计划
                sub_queries, join_sql = self._parse_federated_plan(plan_output)
                
                if not sub_queries:
                    raise ValueError(f"联邦计划生成失败：未能成功提取到有效的子查询节点。 LLM 输出内容:\n{plan_output}")
                if not join_sql:
                    raise ValueError(f"联邦计划生成失败：未能提取到有效的 <memory_join> 逻辑。 LLM 输出内容:\n{plan_output}")
                
                yield {
                    "type": "log",
                    "id": plan_log_id,
                    "title": "生成跨源联邦查询计划",
                    "details": f"联邦计划编排完成，共规划了 {len(sub_queries)} 个子查询。计划明细：\n{plan_output}",
                    "status": "success",
                }
                
            except Exception as e:
                logger.error("[FederatedQueryExecutor] Plan generation failed: %s", e, exc_info=True)
                # 计划生成/XML 解析失败时，只要还有 repair 预算就重试一轮（把原始输出回灌让 LLM 自纠），
                # 不再像以前那样直接 abort。
                if _repair_attempt < MAX_FEDERATED_PLAN_REPAIR_ROUNDS:
                    yield {
                        "type": "log",
                        "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                        "title": "修复联邦查询计划",
                        "details": (
                            "联邦计划生成或 XML 解析失败，正在基于错误重新生成完整联邦计划。"
                            f"\n错误信息: {str(e)}"
                        ),
                        "status": "warning",
                    }
                    repair_context = {
                        "stage": "联邦计划生成",
                        "dataset_name": "plan",
                        "failed_sql": "",
                        "error": str(e),
                        "previous_plan": plan_output,
                    }
                    async for chunk in self.execute(
                        runtime_messages,
                        system_prompt,
                        original_user_question,
                        _repair_attempt=_repair_attempt + 1,
                        _repair_context=repair_context,
                        _original_user_question=original_user_question,
                        _subquery_cache=_subquery_cache,
                    ):
                        yield chunk
                    return
                yield {
                    "type": "log",
                    "id": plan_log_id,
                    "title": "生成联邦查询计划失败",
                    "details": f"错误: {str(e)}",
                    "status": "error",
                }
                yield {
                    "content": f"\n❌ 跨源联邦查询计划编排失败：{str(e)}",
                    "status": "error"
                }
                return

            # 2. 执行子查询并注册到 DuckDB
            duckdb_conn = duckdb.connect(
                database=":memory:",
                config={"enable_external_access": False},
            )
            try:
                user_id = self._current_user_id()
                is_admin = self._current_user_is_admin()
                current_agent_context = get_current_agent_context()
                user_dimensions = (
                    getattr(current_agent_context, "user_dimensions", None)
                    if current_agent_context is not None
                    else None
                )
                
                async with AsyncSessionLocal() as session:
                    perm_service = PermissionService(session)
                    
                    for idx, sq in enumerate(sub_queries):
                        dataset_name = sq["dataset_name"]
                        temp_table = sq["temp_table"]
                        sub_sql = sq["sql"]
                        
                        sub_log_id = f"fed_sub_{uuid.uuid4().hex[:8]}_{idx}"
                        yield {
                            "type": "log",
                            "id": sub_log_id,
                            "title": f"执行子查询 ({dataset_name})",
                            "details": f"正在执行数据集 '{dataset_name}' 的子查询，注册为临时表 '{temp_table}'...",
                            "status": "pending",
                        }
                        
                        try:
                            # 获取数据集并校验权限
                            dataset = await MetadataService.get_dataset_by_name(session, dataset_name)
                            if not dataset:
                                raise ValueError(f"元数据中未找到指定的数据集: '{dataset_name}'。")
                            
                            if not is_admin:
                                if not user_id:
                                    raise PermissionError(f"未提供有效的用户身份，无权访问数据集: '{dataset_name}'")
                                
                                has_perm = await perm_service.check_permission(int(user_id), "metadata", str(dataset.id))
                                if not has_perm:
                                    raise PermissionError(f"无权访问数据集: '{dataset_name}' (ID: {dataset.id})")
                            
                            # 相对时间范围门禁：子查询 SQL 的日期字面量必须与用户问题 + 时间锚点一致。
                            time_range_risk = detect_time_range_mismatch(original_user_question, sub_sql)
                            if time_range_risk:
                                raise ValueError(build_time_range_gate_message(time_range_risk))
                            
                            # 跨 repair 轮缓存命中：相同 (数据集, SQL) 已成功执行过，直接复用结果，避免重跑。
                            cache_key = f"{dataset.name}::{self._normalize_sub_sql(sub_sql)}"
                            cached_res = _subquery_cache.get(cache_key)
                            cache_hit = cached_res is not None
                            if cache_hit:
                                res_str = cached_res
                            else:
                                # 物理执行子查询
                                async with TraceSpanContext(
                                    trace_buffer=self.trace_buffer,
                                    event_type="tool_call",
                                    span_name="execute_sql_query",
                                    tool_name="execute_sql_query",
                                    tool_input={"sql": sub_sql, "data_source": dataset.data_source, "dataset_name": dataset.name}
                                ) as sub_span:
                                    res_str = await execute_sql_query_core(
                                        session,
                                        sql=sub_sql,
                                        data_source=dataset.data_source,
                                        dataset_name=dataset.name,
                                        user_id=int(user_id) if user_id else None,
                                        user_dimensions=user_dimensions or None,
                                        agent_context=current_agent_context,
                                        is_admin=is_admin,
                                        bypass_table_auth=False,
                                    )
                                    sub_span.set_output(res_str)
                            
                            if self._is_sql_tool_error_result(res_str):
                                raise ValueError(res_str)
                            
                            # 成功结果写入缓存供后续 repair 轮复用
                            _subquery_cache[cache_key] = res_str
                            
                            res_data = json.loads(res_str)
                            col_names = [col["name"] for col in res_data.get("columns", [])]
                            raw_items = res_data.get("items") or res_data.get("rows") or []
                            # 注意：子查询结果用更宽松的上限注册，保证 join/聚合的正确性；
                            # 只有最终 join 输出才收敛到 MAX_FEDERATED_ROWS。
                            items = self._limit_rows(raw_items, max_rows=MAX_FEDERATED_SUBQUERY_ROWS)
                            if len(raw_items) > len(items):
                                limit_note = (
                                    f"（原始返回 {len(raw_items)} 行，已截断到 {MAX_FEDERATED_SUBQUERY_ROWS} 行，"
                                    "join/聚合结果可能不完整）"
                                )
                                truncated_notes.append(
                                    f"数据集 '{dataset_name}' 子查询返回 {len(raw_items)} 行，"
                                    f"已截断到 {MAX_FEDERATED_SUBQUERY_ROWS} 行参与关联"
                                )
                            else:
                                limit_note = ""
                            cache_note = "（命中子查询结果缓存，未重跑）" if cache_hit else ""
                            
                            # 注册临时表到 DuckDB
                            df = pd.DataFrame(items, columns=col_names)
                            duckdb_conn.register(temp_table, df)
                            
                            yield {
                                "type": "log",
                                "id": sub_log_id,
                                "title": f"执行子查询 ({dataset_name})",
                                "details": f"子查询执行成功{cache_note}。已导入临时表 '{temp_table}' ({len(items)} 行数据){limit_note}。\nSQL:\n{sub_sql}",
                                "status": "success",
                            }
                            
                        except Exception as e:
                            logger.error(f"[FederatedQueryExecutor] Subquery execution failed on dataset {dataset_name}: {e}", exc_info=True)
                            if idx == 0:
                                if (
                                    _repair_attempt < MAX_FEDERATED_PLAN_REPAIR_ROUNDS
                                    and self._is_retryable_federated_plan_error(e)
                                ):
                                    yield {
                                        "type": "log",
                                        "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                                        "title": "修复联邦查询计划",
                                        "details": (
                                            f"主表子查询 ({dataset_name}) 执行失败，正在基于数据库错误重新生成完整联邦计划。"
                                            f"\n错误信息: {str(e)}\nSQL:\n{sub_sql}"
                                        ),
                                        "status": "warning",
                                    }
                                    repair_context = {
                                        "stage": f"主表子查询 ({dataset_name})",
                                        "dataset_name": dataset_name,
                                        "failed_sql": sub_sql,
                                        "error": str(e),
                                        "previous_plan": plan_output,
                                    }
                                    async for chunk in self.execute(
                                        runtime_messages,
                                        system_prompt,
                                        original_user_question,
                                        _repair_attempt=_repair_attempt + 1,
                                        _repair_context=repair_context,
                                        _original_user_question=original_user_question,
                                        _subquery_cache=_subquery_cache,
                                    ):
                                        yield chunk
                                    return
                                yield {
                                    "type": "log",
                                    "id": sub_log_id,
                                    "title": f"执行主表子查询 ({dataset_name}) 失败",
                                    "details": f"错误信息: {str(e)}\nSQL:\n{sub_sql}",
                                    "status": "error",
                                }
                                yield {
                                    "content": f"\n❌ 跨源联邦主表子查询失败（数据集: '{dataset_name}'）：{str(e)}",
                                    "status": "error"
                                }
                                return
                            else:
                                if self._is_non_degradable_federated_error(e):
                                    yield {
                                        "type": "log",
                                        "id": sub_log_id,
                                        "title": f"执行子查询 ({dataset_name}) 失败",
                                        "details": f"错误信息: {str(e)}\nSQL:\n{sub_sql}",
                                        "status": "error",
                                    }
                                    yield {
                                        "content": f"\n❌ 跨源联邦子查询失败（数据集: '{dataset_name}'）：{str(e)}",
                                        "status": "error",
                                    }
                                    return
                                if (
                                    _repair_attempt < MAX_FEDERATED_PLAN_REPAIR_ROUNDS
                                    and self._is_retryable_federated_plan_error(e)
                                ):
                                    yield {
                                        "type": "log",
                                        "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                                        "title": "修复联邦查询计划",
                                        "details": (
                                            f"子查询 ({dataset_name}) 执行失败，正在基于数据库错误重新生成完整联邦计划。"
                                            f"\n错误信息: {str(e)}\nSQL:\n{sub_sql}"
                                        ),
                                        "status": "warning",
                                    }
                                    repair_context = {
                                        "stage": f"子查询 ({dataset_name})",
                                        "dataset_name": dataset_name,
                                        "failed_sql": sub_sql,
                                        "error": str(e),
                                        "previous_plan": plan_output,
                                    }
                                    async for chunk in self.execute(
                                        runtime_messages,
                                        system_prompt,
                                        original_user_question,
                                        _repair_attempt=_repair_attempt + 1,
                                        _repair_context=repair_context,
                                        _original_user_question=original_user_question,
                                        _subquery_cache=_subquery_cache,
                                    ):
                                        yield chunk
                                    return
                                degraded_datasets.append(dataset_name)
                                yield {
                                    "type": "log",
                                    "id": sub_log_id,
                                    "title": f"执行子查询 ({dataset_name}) 失败，已自动降级",
                                    "details": f"警告: 关联数据集 '{dataset_name}' 的查询失败，相关字段已自动降级留空处理。错误信息: {str(e)}\nSQL:\n{sub_sql}",
                                    "status": "warning",
                                }
                                yield {
                                    "content": f"\n⚠️ 警告: 关联数据集 '{dataset_name}' 的查询失败，相关字段已自动降级留空处理。",
                                    "status": "warning"
                                }
                                
                                # 使用 sqlglot 解析失败子查询的 SQL 字段
                                col_names = []
                                try:
                                    import sqlglot
                                    from sqlglot import exp
                                    dialect = dataset_dialect_map.get(dataset_name) if dataset_dialect_map else None
                                    parsed = sqlglot.parse(sub_sql, read=dialect)
                                    if parsed:
                                        expression = parsed[0]
                                        if isinstance(expression, exp.Select):
                                            cols = []
                                            for expr in expression.expressions:
                                                alias = getattr(expr, "alias", None)
                                                name = getattr(expr, "name", None)
                                                if isinstance(expr, exp.Alias):
                                                    cols.append(expr.alias)
                                                elif isinstance(expr, exp.Column):
                                                    cols.append(expr.name)
                                                elif alias:
                                                    cols.append(alias)
                                                elif name:
                                                    cols.append(name)
                                                else:
                                                    cols.append(expr.sql(dialect))
                                            col_names = [c.strip('`"\'') for c in cols if c]
                                except Exception as parse_err:
                                    logger.warning(
                                        "[FederatedQueryExecutor] Failed to parse columns from failed subquery SQL: %s",
                                        parse_err,
                                        exc_info=True
                                    )
                                
                                df = pd.DataFrame(columns=col_names)
                                duckdb_conn.register(temp_table, df)
                                continue

                # 3. 内存 Join 联合计算
                join_log_id = f"fed_join_{uuid.uuid4().hex[:8]}"
                yield {
                    "type": "log",
                    "id": join_log_id,
                    "title": "内存联邦聚合计算",
                    "details": "正在 DuckDB 内存中对所有临时表执行多源关联与最终汇总计算...",
                    "status": "pending",
                }
                
                try:
                    join_error = self._validate_memory_join_sql(join_sql)
                    if join_error:
                        raise ValueError(join_error)
                    # 执行 DuckDB 关联 SQL
                    duck_res = duckdb_conn.execute(join_sql)
                    raw_join_rows = duck_res.fetchall()
                    join_rows = self._limit_rows(raw_join_rows)
                    columns = self._columns_from_duckdb_description(duck_res.description)
                    if len(raw_join_rows) > len(join_rows):
                        limit_note = f"（原始产出 {len(raw_join_rows)} 行，已截断到 {MAX_FEDERATED_ROWS} 行）"
                        truncated_notes.append(
                            f"最终关联结果共 {len(raw_join_rows)} 行，仅展示前 {MAX_FEDERATED_ROWS} 行"
                        )
                    else:
                        limit_note = ""
                    
                    final_data = {
                        "columns": columns,
                        "items": [list(r) for r in join_rows]
                    }
                    
                    yield {
                        "type": "log",
                        "id": join_log_id,
                        "title": "内存联邦聚合计算",
                        "details": f"内存关联计算成功。汇总产出 {len(join_rows)} 行数据{limit_note}。\nSQL:\n{join_sql}",
                        "status": "success",
                    }
                    
                except Exception as e:
                    logger.error("[FederatedQueryExecutor] Memory join failed: %s", e, exc_info=True)
                    if (
                        _repair_attempt < MAX_FEDERATED_PLAN_REPAIR_ROUNDS
                        and self._is_retryable_federated_plan_error(e)
                    ):
                        yield {
                            "type": "log",
                            "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                            "title": "修复联邦查询计划",
                            "details": (
                                "内存联邦聚合 SQL 执行失败，正在基于错误重新生成完整联邦计划。"
                                f"\n错误信息: {str(e)}\nSQL:\n{join_sql}"
                            ),
                            "status": "warning",
                        }
                        repair_context = {
                            "stage": "内存联邦聚合计算",
                            "dataset_name": "federated",
                            "failed_sql": join_sql,
                            "error": str(e),
                            "previous_plan": plan_output,
                        }
                        async for chunk in self.execute(
                            runtime_messages,
                            system_prompt,
                            original_user_question,
                            _repair_attempt=_repair_attempt + 1,
                            _repair_context=repair_context,
                            _original_user_question=original_user_question,
                            _subquery_cache=_subquery_cache,
                        ):
                            yield chunk
                        return
                    yield {
                        "type": "log",
                        "id": join_log_id,
                        "title": "内存联邦聚合计算失败",
                        "details": f"错误信息: {str(e)}\nSQL:\n{join_sql}",
                        "status": "error",
                    }
                    yield {
                        "content": f"\n❌ 内存联邦计算失败：{str(e)}",
                        "status": "error"
                    }
                    return

                # 4. 可视化分析与总结解读
                final_md_table = make_markdown_table(columns, join_rows)
                
                # 保存至 "上一轮数据结果" 中供下一次追问与微调
                try:
                    await self.agent_runner._save_last_data_result_for_followups(
                        {
                            "sql": join_sql,
                            "data_source": "duckdb",
                            "dataset_name": "federated"
                        },
                        final_data
                    )
                except Exception as e:
                    logger.warning("[FederatedQueryExecutor] Failed to save last data result: %s", e)

                # 调用 LLM 做最终的分析和可视化（附带数据完整性提示，避免把降级/截断结果当完整结论）
                data_caveats = self._build_data_caveats(degraded_datasets, truncated_notes)
                synthesis_prompt = DataQueryPrompts.build_federated_synthesis_prompt(
                    original_user_question,
                    final_md_table,
                    data_caveats=data_caveats,
                    dataset_names=self.datasets,
                )
                
                # 流式输出总结（synthesis_prompt 已含用户问题，user_prompt 用默认值避免重复）
                llm_stream = await AgentConfigProvider.get_configured_llm(
                    streaming=True,
                    config=getattr(self.agent_runner, "config", None),
                )
                chat_client_stream = chat_client_from_handle(llm_stream)
                synthesis_messages = system_user_prompt_messages(synthesis_prompt)
                
                chunk_count = 0
                async for msg_chunk in chat_client_stream.stream_messages(synthesis_messages):
                    text = msg_chunk.content if isinstance(msg_chunk.content, str) else ""
                    if text:
                        chunk_count += 1
                        yield {"content": text}
                
                logger.info("[FederatedQueryExecutor] Synthesis complete. Total text chunks yielded: %d", chunk_count)
                yield {"content": "", "status": "success"}
            finally:
                duckdb_conn.close()

    @staticmethod
    def _is_sql_tool_error_result(output: Any) -> bool:
        text = str(output or "").lstrip()
        return text.startswith(
            (
                "[TOOL_ERROR]",
                "[Validation Failed]",
                "[Permission Denied]",
                "[Security Error]",
                "[Performance Blocked]",
            )
        )

    @staticmethod
    def _is_retryable_federated_plan_error(error: Any) -> bool:
        text = str(error or "")
        if not text.strip():
            return False
        lower = text.lower()
        non_retryable_markers = (
            "permission denied",
            "unauthorized",
            "access denied",
            "无权访问",
            "未提供有效的用户身份",
            "权限不足",
            "禁止外部访问",
            "外部访问",
            "只允许单条 select",
            "只允许 select/with",
            "只允许 select",
            "[security error]",
        )
        if any(marker in lower for marker in non_retryable_markers):
            return False
        # 复用单源链路的日期/字段引用 detector，保持判定口径一致
        try:
            from app.services.ai.runners.data_agent_runner import DataAgentRunner
            if DataAgentRunner._is_date_format_sql_error(text) or DataAgentRunner._is_schema_reference_sql_error(text):
                return True
        except Exception:
            pass
        retryable_markers = (
            "[validation failed]",
            "[performance blocked]",
            "[tool_error]",
            "ora-",
            "unknown column",
            "unknown table",
            "invalid identifier",
            "invalid number",
            "syntax",
            "unexpected token",
            "no such column",
            "no such table",
            "does not exist",
            # GROUP BY / 聚合类
            "not a group by",
            "group by expression",
            "ora-00979",
            # 连接 / 超时 / 网络类（瞬时或可改写的执行错误）
            "timeout",
            "timed out",
            "lock wait",
            "connection",
            "connect",
            "数据库连接",
            "执行超时",
            "除零",
            "division by zero",
            "time_range_gate",
            "相对时间",
            "时间锚点",
            "时间范围与用户相对时间",
        )
        return any(marker in lower for marker in retryable_markers)

    @staticmethod
    def _is_non_degradable_federated_error(error: Any) -> bool:
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
        )
        return any(marker in lower for marker in markers)

    @staticmethod
    def _build_federated_plan_repair_prompt(
        base_prompt: str,
        repair_context: Dict[str, Any],
        repair_attempt: int,
    ) -> str:
        error = str(repair_context.get("error") or "").strip()
        failed_sql = str(repair_context.get("failed_sql") or "").strip()
        previous_plan = str(repair_context.get("previous_plan") or "").strip()
        stage = str(repair_context.get("stage") or "联邦查询执行").strip()
        dataset_name = str(repair_context.get("dataset_name") or "").strip()
        # 命中日期/时间格式或字段引用类错误时，复用单源链路同一套 detector 追加专项修正指引，
        # 避免 LLM 在 repair 时反复生成同样的 TO_DATE 包裹或臆造字段导致再次失败。
        targeted_guides = ""
        is_date_err = False
        is_schema_err = False
        try:
            from app.services.ai.runners.data_agent_runner import DataAgentRunner
            is_date_err = DataAgentRunner._is_date_format_sql_error(error)
            is_schema_err = DataAgentRunner._is_schema_reference_sql_error(error)
        except Exception:
            error_lower = error.lower()
            is_date_err = any(
                m in error_lower for m in ("ora-01861", "ora-01830", "literal does not match format string")
            )
            is_schema_err = any(
                m in error_lower
                for m in ("unknown column", "unknown table", "invalid identifier", "no such column", "no such table")
            )
        if is_date_err:
            targeted_guides += f"\n\n{DataQueryPrompts.DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE}"
        if is_schema_err:
            targeted_guides += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
        if TIME_RANGE_GATE_PREFIX in error or "相对时间" in error.lower() or "时间锚点" in error:
            targeted_guides += (
                f"\n\n{build_data_query_time_anchor_block()}\n\n"
                f"{DataQueryPrompts.TIME_RANGE_ANOMALY_REPAIR_GUIDE}"
            )
        return (
            f"{base_prompt}\n\n"
            "【上一轮联邦计划执行失败，需要 repair 后重试】\n"
            f"repair_attempt: {repair_attempt}\n"
            f"失败阶段: {stage}\n"
            f"失败数据集: {dataset_name or '未知'}\n"
            f"错误信息:\n{error[:2000]}\n\n"
            f"失败 SQL:\n{failed_sql[:4000]}\n\n"
            f"上一轮完整联邦计划:\n{previous_plan[:8000]}\n\n"
            "【本轮 repair 要求】\n"
            "1. 必须重新输出完整 `<multi_dataset_plan>` XML，不要只输出局部 SQL 或解释文字。\n"
            "2. 禁止原样重复失败 SQL；必须围绕错误信息最小化修正字段名、表名、JOIN、WHERE、日期转换、时间边界或聚合逻辑。\n"
            "3. 如果错误包含 ORA-01722 / invalid number，优先检查隐式数字转换、DATE/TIMESTAMP 字段被错误包裹函数、"
            "字符字段与数字字段比较等问题；Oracle 日期筛选优先使用 DATE 字面量范围，例如 "
            "`date_col >= DATE '2026-05-01' AND date_col < DATE '2026-06-01'`。\n"
            "4. 如果错误发生在 `<memory_join>`，请同步校正各 `<sub_query>` 的投影别名与 `<memory_join>` 引用字段，"
            "确保临时表字段一致。\n"
            "5. 不得通过扩大权限、跨数据集直连 JOIN、外部文件访问或写操作绕过平台约束。"
            f"{targeted_guides}"
        )

    def _current_user_id(self) -> Optional[int]:
        resolver = getattr(self.agent_runner, "_current_user_id", None)
        if callable(resolver):
            return resolver()
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    def _current_user_is_admin(self) -> bool:
        resolver = getattr(self.agent_runner, "_current_user_is_admin", None)
        if callable(resolver):
            return bool(resolver())
        if not self.user_info:
            return False
        if bool(self.user_info.get("is_admin")):
            return True
        role = str(
            self.user_info.get("role")
            or self.user_info.get("role_name")
            or ""
        ).strip().lower()
        return role in {"admin", "administrator", "平台管理员"}

    @staticmethod
    def _limit_rows(rows: list[Any], *, max_rows: int = MAX_FEDERATED_ROWS) -> list[Any]:
        if not isinstance(rows, list):
            return []
        return rows[:max_rows]

    @staticmethod
    def _normalize_sub_sql(sql: str) -> str:
        """子查询缓存 key 用的归一化（小写 + 折叠空白），仅用于完全相同 SQL 的复用判定。"""
        return " ".join(str(sql or "").strip().lower().split())

    @staticmethod
    def _build_data_caveats(degraded_datasets: list[str], truncated_notes: list[str]) -> str:
        """汇总降级/截断情况，供 synthesis prompt 显式标注数据完整性，避免把不完整结果当完整结论。"""
        parts: list[str] = []
        if degraded_datasets:
            uniq = list(dict.fromkeys(degraded_datasets))
            parts.append(
                "以下关联数据集查询失败，相关字段已降级留空，可能缺失："
                + "、".join(uniq)
            )
        if truncated_notes:
            uniq_notes = list(dict.fromkeys(truncated_notes))
            parts.append("以下数据存在行数截断，统计/汇总口径可能不完整：" + "；".join(uniq_notes))
        return "\n".join(parts)

    @staticmethod
    def _validate_memory_join_sql(sql: str) -> Optional[str]:
        sql_text = str(sql or "").strip()
        if not sql_text:
            return "内存联邦 SQL 为空。"
        sql_no_comments = re.sub(r"(--[^\n]*|/\*.*?\*/)", " ", sql_text, flags=re.DOTALL)
        if ";" in sql_no_comments.rstrip(";"):
            return "内存联邦 SQL 只允许单条 SELECT 查询。"
        if not re.match(r"^(SELECT|WITH)\b", sql_no_comments.strip(), flags=re.IGNORECASE):
            return "内存联邦 SQL 只允许 SELECT/WITH 查询。"
        blocked = re.search(
            r"\b(COPY|INSTALL|LOAD|ATTACH|DETACH|EXPORT|IMPORT|CREATE|INSERT|UPDATE|DELETE|DROP|ALTER|PRAGMA|CALL|"
            r"READ_CSV|READ_CSV_AUTO|READ_PARQUET|READ_JSON|READ_JSON_AUTO|READ_TEXT|READ_BLOB|HTTPFS)\b",
            sql_no_comments,
            flags=re.IGNORECASE,
        )
        if blocked:
            return f"内存联邦 SQL 禁止外部访问或写操作: {blocked.group(1)}"
        return None

    @staticmethod
    def _duckdb_type_to_result_type(type_name: Any) -> str:
        text = str(type_name or "").upper()
        if any(token in text for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "HUGEINT", "UBIGINT")):
            return "number"
        if "BOOL" in text:
            return "boolean"
        if any(token in text for token in ("DATE", "TIME")):
            return "datetime"
        return "string"

    @classmethod
    def _columns_from_duckdb_description(cls, description: Any) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for desc in description or []:
            name = str(desc[0]) if desc else ""
            type_name = desc[1] if len(desc) > 1 else ""
            columns.append({"name": name, "type": cls._duckdb_type_to_result_type(type_name)})
        return columns

    @staticmethod
    def _extract_dialect_map(schema_output: str) -> dict[str, str]:
        """从合并 Schema YAML 文本中提取 dataset_name -> data_source 的方言映射。
        
        Schema 每个 table chunk 中包含相邻的两行：
          dataset: <dataset_name>
          ...
          data_source: <type>   (如 mysql, clickhouse, oracle)
        
        此函数扫描所有这样的组合并去重，返回 {dataset_name: data_source} 字典。
        """
        dialect_map: dict[str, str] = {}
        # 匹配 "dataset: xxx" 后（允许中间有若干行）紧跟着 "data_source: yyy"
        # 因为 YAML 中 dataset 和 data_source 在同一 block 内出现，逐行扫描更健壮
        lines = schema_output.splitlines()
        current_dataset: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("dataset:"):
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_dataset = parts[1].strip()
            elif stripped.startswith("data_source:") and current_dataset:
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    ds_type = parts[1].strip()
                    if ds_type and current_dataset not in dialect_map:
                        dialect_map[current_dataset] = ds_type
                    # 读到 data_source 后重置，避免错误归属
                    current_dataset = None
        return dialect_map

    def _parse_federated_plan(self, xml_content: str) -> tuple[list[dict], str]:
        """解析联邦 XML 执行计划，采用正则表达式容错提取"""
        sub_queries = []
        
        # 匹配 <sub_query dataset_name="..." temp_table="...">SQL</sub_query>
        sub_query_matches = re.finditer(
            r'<sub_query\s+dataset_name=["\']([^"\']+)["\']\s+temp_table=["\']([^"\']+)["\']\s*>(.*?)</sub_query>',
            xml_content,
            re.DOTALL | re.IGNORECASE
        )
        
        for m in sub_query_matches:
            ds_name = m.group(1).strip()
            temp_tb = m.group(2).strip()
            sql_content = m.group(3).strip()
            
            # 去掉 CDATA 包裹
            if sql_content.startswith("<![CDATA[") and sql_content.endswith("]]>"):
                sql_content = sql_content[9:-3].strip()
            elif sql_content.startswith("<![CDATA["):
                sql_content = sql_content[9:].strip()
            elif sql_content.endswith("]]>"):
                sql_content = sql_content[:-3].strip()
                
            # HTML 字符实体反转义
            sql_content = html.unescape(sql_content).strip()
            
            sub_queries.append({
                "dataset_name": ds_name,
                "temp_table": temp_tb,
                "sql": sql_content
            })
            
        join_match = re.search(r'<memory_join\s*>(.*?)</memory_join>', xml_content, re.DOTALL | re.IGNORECASE)
        join_sql = ""
        if join_match:
            join_sql = join_match.group(1).strip()
            if join_sql.startswith("<![CDATA[") and join_sql.endswith("]]>"):
                join_sql = join_sql[9:-3].strip()
            elif join_sql.startswith("<![CDATA["):
                join_sql = join_sql[9:].strip()
            elif join_sql.endswith("]]>"):
                join_sql = join_sql[:-3].strip()
                
            join_sql = html.unescape(join_sql).strip()
            
        return sub_queries, join_sql
