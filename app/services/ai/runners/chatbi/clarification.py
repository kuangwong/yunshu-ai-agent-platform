"""ChatBI clarification — extracted from DataAgentRunner."""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List

from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)


async def generate_clarification_content(
    runner: Any,
    *,
    user_question: str,
    history: List[Dict[str, str]],
    reasoning: str,
    ) -> str:
    history_excerpt = DataQueryPrompts.format_clarification_history(history)
    user_profile = None
    if runner.user_info:
        from app.services.ai.agent_prompts import AgentServicePrompts
        raw_name = runner.user_info.get("user_name") or runner.user_info.get("username", "Unknown User")
        user_id = str(runner.user_info.get("user_id") or runner.user_info.get("id") or "")
        real_name = runner.user_info.get("real_name") or raw_name
        dept = runner.user_info.get("dept_name") or runner.user_info.get("department")
        org_path = runner.user_info.get("org_path")
        dept_code = runner.user_info.get("dept_code")
        role = runner.user_info.get("role_name") or runner.user_info.get("role")

        user_profile = AgentServicePrompts.user_context_message(
            user_id=user_id or "unknown",
            raw_name=raw_name,
            real_name=real_name,
            dept=dept,
            dept_code=dept_code,
            org_path=org_path,
            role=role,
        )

    fallback = DataQueryPrompts.build_clarification_fallback(
        user_question,
        reasoning,
        history_excerpt,
    )
    try:
        llm = await AgentConfigProvider.get_configured_llm(
            streaming=False,
            config=runner.config,
        )
        chat_client = chat_client_from_handle(llm)
        content = await chat_client.generate_text(
            system_user_prompt_messages(
                DataQueryPrompts.clarification_generation_prompt(
                    user_question,
                    reasoning,
                    history_excerpt,
                    user_profile=user_profile,
                ),
                user_prompt=user_question,
            )
        )
        cleaned = str(content or "").strip()
        if cleaned and DataQueryPrompts.has_quick_suggestions(cleaned):
            return finalize_visible_reply(
                DataQueryPrompts.ensure_clarification_reason_block(
                    cleaned,
                    user_question,
                    reasoning,
                ),
                collapse_duplicates=False,
            )
        if cleaned:
            merged = DataQueryPrompts.append_contextual_quick_suggestions(
                cleaned,
                user_question,
                reasoning,
                history_excerpt,
            )
            if DataQueryPrompts.has_quick_suggestions(merged):
                return finalize_visible_reply(
                    DataQueryPrompts.ensure_clarification_reason_block(
                        merged,
                        user_question,
                        reasoning,
                    ),
                    collapse_duplicates=False,
                )
    except Exception as e:
        logger.warning("[DataAgentRunner] Contextual clarification generation failed: %s", e)
    return finalize_visible_reply(
        DataQueryPrompts.ensure_clarification_reason_block(
            fallback,
            user_question,
            reasoning,
        ),
        collapse_duplicates=False,
    )

async def yield_contextual_clarification(
    runner: Any,
    *,
    user_question: str,
    history: List[Dict[str, str]],
    reasoning: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
    yield {
        "type": "log",
        "id": f"clarify_{uuid.uuid4().hex[:8]}",
        "title": "需要补充查数信息",
        "details": reasoning,
        "status": "warning",
        "category": "intent",
    }
    content = await runner._generate_clarification_content(
        user_question=user_question,
        history=history,
        reasoning=reasoning,
    )
    yield {"content": content, "status": "success"}

async def yield_missing_reusable_result_clarification(
    runner: Any,
    history: List[Dict[str, str]],
    *,
    user_question: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
    history_excerpt = DataQueryPrompts.format_clarification_history(history)
    reasoning = (
        "检测到本轮是基于上一轮结果的分析/可视化请求，"
        "但当前会话没有保存的结构化查询结果。"
    )
    yield {
        "type": "log",
        "id": f"reuse_miss_{uuid.uuid4().hex[:8]}",
        "title": "缺少可复用查询结果",
        "details": reasoning,
        "status": "error",
    }
    yield {
        "content": DataQueryPrompts.build_missing_reusable_result_fallback(
            history_excerpt,
            user_question=user_question,
        ),
        "status": "success",
    }

