"""ChatBI synthesis — extracted from DataAgentRunner."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

from app.schemas.agent import AgentExecutionStep
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.common import extract_tokens_from_message, normalize_messages_for_llm
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply
from app.services.ai.runners.chatbi.run_state import DataRunState

logger = logging.getLogger(__name__)


async def synthesize_from_last_data_result(
    runner: Any,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    last_result: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"reuse_{uuid.uuid4().hex[:8]}",
        "title": "复用上一轮查询结果",
        "details": "检测到本轮是基于上一轮结果的分析/可视化请求，已跳过重新检索 Schema 与执行 SQL。",
        "status": "success",
    }
    yield {"type": "thinking", "status": "continuing"}

    prompt_without_menu = (system_prompt or "").replace(
        "{dataset_menu}",
        DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
    )
    safe_result = dict(last_result)
    for r_key in ("rows", "items", "data", "records"):
        val = safe_result.get(r_key)
        if isinstance(val, list) and len(val) > 50:
            safe_result[r_key] = val[:50]
            safe_result["_display_note"] = "部分明细数据由于上下文长度限制已在此处被省略..."
            break
    result_json = json.dumps(safe_result, ensure_ascii=False, indent=2)

    from app.services.ai.runtime.agentscope.compat import HumanMessage

    synthesis_messages = [SystemMessage(content=prompt_without_menu)]
    # 只保留用户追问，不把上一轮 assistant 全文（含图表/表格）塞进 prompt，避免模型照抄两遍。
    synthesis_messages.extend(
        message
        for message in runtime_messages[-6:-1]
        if isinstance(message, HumanMessage) and getattr(message, "content", None)
    )
    synthesis_messages.append(
        HumanMessage(content=DataQueryPrompts.followup_synthesis_user_message(user_question, result_json))
    )

    final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=runner.config)
    full_synthesis_content = ""
    content_emitted = False
    generation_start = None
    gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
    last_synthesis_chunk = None
    try:
        async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
            last_synthesis_chunk = chunk
            content = str(getattr(chunk, "content", "") or "")
            if not content:
                continue
            if not content_emitted:
                generation_start = time.time()
                content_emitted = True
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 开始生成回复",
                    "status": "pending",
                    "started_at": int(generation_start * 1000),
                }
            full_synthesis_content += content
            yield {"content": content}
        if generation_start:
            yield {
                "type": "log",
                "id": gen_log_id,
                "title": "✨ 生成回复完成",
                "status": "success",
                "execution_time_ms": (time.time() - generation_start) * 1000,
            }
    except Exception as syn_err:
        logger.error("[DataAgentRunner] Follow-up synthesis failed: %s", syn_err)
        fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
        full_synthesis_content = fallback
        yield {
            "type": "log",
            "id": f"syn_err_{uuid.uuid4().hex[:6]}",
            "title": "⚠️ 总结生成失败",
            "details": str(syn_err),
            "status": "error",
        }
        yield {"content": fallback}

    deduped_synthesis = finalize_visible_reply(full_synthesis_content)
    if deduped_synthesis != full_synthesis_content:
        logger.warning(
            "[DataAgentRunner] Collapsed duplicated follow-up synthesis output (len %s -> %s)",
            len(full_synthesis_content),
            len(deduped_synthesis),
        )
        full_synthesis_content = deduped_synthesis
        if content_emitted:
            yield {"type": "retraction", "content": full_synthesis_content}

    synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="synthesis",
            agent_name=runner.config.agent_name,
            model=str(getattr(final_llm, "model_name", runner.config.synthesis_model_name or runner.config.model_name)),
            temperature=float(runner.config.synthesis_temperature or runner.config.temperature or 0),
            tool_output={"content": full_synthesis_content, "reused_last_data_result": True},
            raw_log=full_synthesis_content,
            prompt_tokens=synthesis_tokens["prompt_tokens"],
            completion_tokens=synthesis_tokens["completion_tokens"],
            total_tokens=synthesis_tokens["total_tokens"],
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            timestamp=datetime.fromtimestamp(start_synthesis),
        )
    )

async def synthesize_format_correction(
    runner: Any,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    last_result: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"format_{uuid.uuid4().hex[:8]}",
        "title": "样式与图表微调",
        "details": "检测到图表样式或展示微调请求，直接复用上一轮数据，无需重新查数。",
        "status": "success",
    }
    yield {"type": "thinking", "status": "continuing"}

    prompt_without_menu = (system_prompt or "").replace(
        "{dataset_menu}",
        DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
    )
    safe_result = dict(last_result)
    for r_key in ("rows", "items", "data", "records"):
        val = safe_result.get(r_key)
        if isinstance(val, list) and len(val) > 50:
            safe_result[r_key] = val[:50]
            safe_result["_display_note"] = "部分明细数据由于上下文长度限制已在此处被省略..."
            break
    result_json = json.dumps(safe_result, ensure_ascii=False, indent=2)

    from app.services.ai.runtime.agentscope.compat import HumanMessage

    synthesis_messages = [SystemMessage(content=prompt_without_menu)]
    synthesis_messages.extend(
        message
        for message in runtime_messages[-6:-1]
        if isinstance(message, HumanMessage) and getattr(message, "content", None)
    )
    synthesis_messages.append(
        HumanMessage(content=DataQueryPrompts.format_correction_user_message(user_question, result_json))
    )

    final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=runner.config)
    full_synthesis_content = ""
    content_emitted = False
    generation_start = None
    gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
    last_synthesis_chunk = None
    try:
        async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
            last_synthesis_chunk = chunk
            content = str(getattr(chunk, "content", "") or "")
            if not content:
                continue
            if not content_emitted:
                generation_start = time.time()
                content_emitted = True
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 开始生成微调样式",
                    "status": "pending",
                    "started_at": int(generation_start * 1000),
                }
            full_synthesis_content += content
            yield {"content": content}
        if generation_start:
            yield {
                "type": "log",
                "id": gen_log_id,
                "title": "✨ 微调样式生成完成",
                "status": "success",
                "execution_time_ms": (time.time() - generation_start) * 1000,
            }
    except Exception as syn_err:
        logger.error("[DataAgentRunner] Chart format correction synthesis failed: %s", syn_err)
        fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
        full_synthesis_content = fallback
        yield {
            "type": "log",
            "id": f"syn_err_{uuid.uuid4().hex[:6]}",
            "title": "⚠️ 样式生成失败",
            "details": str(syn_err),
            "status": "error",
        }
        yield {"content": fallback}

    deduped_synthesis = finalize_visible_reply(full_synthesis_content)
    if deduped_synthesis != full_synthesis_content:
        full_synthesis_content = deduped_synthesis
        if content_emitted:
            yield {"type": "retraction", "content": full_synthesis_content}

    synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="synthesis",
            agent_name=runner.config.agent_name,
            model=str(getattr(final_llm, "model_name", runner.config.synthesis_model_name or runner.config.model_name)),
            temperature=float(runner.config.synthesis_temperature or runner.config.temperature or 0),
            tool_output={"content": full_synthesis_content, "reused_last_data_result": True},
            raw_log=full_synthesis_content,
            prompt_tokens=synthesis_tokens["prompt_tokens"],
            completion_tokens=synthesis_tokens["completion_tokens"],
            total_tokens=synthesis_tokens["total_tokens"],
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            timestamp=datetime.fromtimestamp(start_synthesis),
        )
    )

async def synthesize_from_history_data_result(
    runner: Any,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"reuse_hist_{uuid.uuid4().hex[:8]}",
        "title": "复用上一轮查询结果",
        "details": (
            "检测到本轮是基于上一轮结果的分析/可视化请求；结构化缓存暂不可用，"
            "已基于最近对话中的查数展示继续处理。"
        ),
        "status": "success",
    }
    yield {"type": "thinking", "status": "continuing"}

    history_excerpt = runner._latest_data_assistant_excerpt(history)
    prompt_without_menu = (system_prompt or "").replace(
        "{dataset_menu}",
        DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
    )

    from app.services.ai.runtime.agentscope.compat import HumanMessage

    synthesis_messages = [SystemMessage(content=prompt_without_menu)]
    synthesis_messages.extend(
        message
        for message in runtime_messages[-6:-1]
        if isinstance(message, HumanMessage) and getattr(message, "content", None)
    )
    synthesis_messages.append(
        HumanMessage(
            content=DataQueryPrompts.followup_synthesis_from_history_user_message(
                user_question,
                history_excerpt,
            )
        )
    )

    final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=runner.config)
    full_synthesis_content = ""
    content_emitted = False
    generation_start = None
    gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
    last_synthesis_chunk = None
    try:
        async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
            last_synthesis_chunk = chunk
            content = str(getattr(chunk, "content", "") or "")
            if not content:
                continue
            if not content_emitted:
                generation_start = time.time()
                content_emitted = True
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 开始生成回复",
                    "status": "pending",
                    "started_at": int(generation_start * 1000),
                }
            full_synthesis_content += content
            yield {"content": content}
        if generation_start:
            yield {
                "type": "log",
                "id": gen_log_id,
                "title": "✨ 生成回复完成",
                "status": "success",
                "execution_time_ms": (time.time() - generation_start) * 1000,
            }
    except Exception as syn_err:
        logger.error("[DataAgentRunner] History follow-up synthesis failed: %s", syn_err)
        fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
        full_synthesis_content = fallback
        yield {
            "type": "log",
            "id": f"syn_err_{uuid.uuid4().hex[:6]}",
            "title": "⚠️ 总结生成失败",
            "details": str(syn_err),
            "status": "error",
        }
        yield {"content": fallback}

    deduped_synthesis = finalize_visible_reply(full_synthesis_content)
    if deduped_synthesis != full_synthesis_content:
        full_synthesis_content = deduped_synthesis
        if content_emitted:
            yield {"type": "retraction", "content": full_synthesis_content}

    synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="synthesis",
            agent_name=runner.config.agent_name,
            model=str(getattr(final_llm, "model_name", runner.config.synthesis_model_name or runner.config.model_name)),
            temperature=float(runner.config.synthesis_temperature or runner.config.temperature or 0),
            tool_output={"content": full_synthesis_content, "reused_history_data_result": True},
            raw_log=full_synthesis_content,
            prompt_tokens=synthesis_tokens["prompt_tokens"],
            completion_tokens=synthesis_tokens["completion_tokens"],
            total_tokens=synthesis_tokens["total_tokens"],
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            timestamp=datetime.fromtimestamp(start_synthesis),
        )
    )

async def synthesize_from_cached_sql_result(
    runner: Any,
    *,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    state: DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"repeat_sql_{uuid.uuid4().hex[:8]}",
        "title": "复用已执行 SQL 结果",
        "details": "检测到模型重复调用相同 SQL。平台已拦截重复执行，并基于首次成功查询结果生成最终回答。",
        "status": "success",
    }

    raw_result = state.last_successful_sql_output
    parsed_result = runner._try_parse_json_output(raw_result)
    result_json = json.dumps(parsed_result, ensure_ascii=False, indent=2, default=str)
    if len(result_json) > 20000:
        result_json = result_json[:20000] + "\n... [SQL 结果过长已截断]"

    execution_review = (
        "【执行过程回顾】\n"
        "- 已成功执行 SQL 并获得非空结果。\n"
        "- 随后模型重复调用相同 SQL，平台已拦截重复执行并复用首次成功查询结果。\n\n"
        "【查询结果】\n"
        f"{result_json}"
    )
    prompt_without_menu = (system_prompt or "").replace(
        "{dataset_menu}",
        DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
    )
    synthesis_messages = [SystemMessage(content=prompt_without_menu)]
    synthesis_messages.extend(
        message
        for message in runtime_messages[-6:-1]
        if isinstance(message, HumanMessage) and getattr(message, "content", None)
    )
    synthesis_messages.append(
        HumanMessage(content=DataQueryPrompts.synthesis_user_message(user_question, execution_review))
    )

    final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=runner.config)
    full_synthesis_content = ""
    content_emitted = False
    generation_start = None
    gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
    last_synthesis_chunk = None
    try:
        async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
            last_synthesis_chunk = chunk
            content = str(getattr(chunk, "content", "") or "")
            if not content:
                continue
            if not content_emitted:
                generation_start = time.time()
                content_emitted = True
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 开始生成回复",
                    "status": "pending",
                    "started_at": int(generation_start * 1000),
                }
            full_synthesis_content += content
            yield {"content": content}
        if generation_start:
            yield {
                "type": "log",
                "id": gen_log_id,
                "title": "✨ 生成回复完成",
                "status": "success",
                "execution_time_ms": (time.time() - generation_start) * 1000,
            }
    except Exception as syn_err:
        logger.error("[DataAgentRunner] Cached SQL synthesis failed: %s", syn_err)
        fallback = DataQueryPrompts.SYNTHESIS_FAILED_FALLBACK
        full_synthesis_content = fallback
        yield {
            "type": "log",
            "id": f"syn_err_{uuid.uuid4().hex[:6]}",
            "title": "⚠️ 总结生成失败",
            "details": str(syn_err),
            "status": "error",
        }
        yield {"content": fallback}

    deduped_synthesis = finalize_visible_reply(full_synthesis_content)
    if deduped_synthesis != full_synthesis_content:
        logger.warning(
            "[DataAgentRunner] Collapsed duplicated cached SQL synthesis output (len %s -> %s)",
            len(full_synthesis_content),
            len(deduped_synthesis),
        )
        full_synthesis_content = deduped_synthesis
        if content_emitted:
            yield {"type": "retraction", "content": full_synthesis_content}

    synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="synthesis",
            agent_name=runner.config.agent_name,
            model=str(getattr(final_llm, "model_name", runner.config.synthesis_model_name or runner.config.model_name)),
            temperature=float(runner.config.synthesis_temperature or runner.config.temperature or 0),
            tool_output={"content": full_synthesis_content, "reused_repeated_sql_result": True},
            raw_log=full_synthesis_content,
            prompt_tokens=synthesis_tokens["prompt_tokens"],
            completion_tokens=synthesis_tokens["completion_tokens"],
            total_tokens=synthesis_tokens["total_tokens"],
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            timestamp=datetime.fromtimestamp(start_synthesis),
        )
    )

