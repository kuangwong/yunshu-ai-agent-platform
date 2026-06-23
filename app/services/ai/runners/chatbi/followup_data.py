"""ChatBI follow-up data result persistence."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def load_last_data_result(runner: Any) -> Optional[Dict[str, Any]]:
    if not runner.conversation_id:
        return None
    user_id = runner._current_user_id()
    if not user_id:
        return None
    try:
        from app.services.ai.memory_service import memory_service

        return await memory_service.get_last_data_result(user_id, runner.conversation_id)
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to load last data result: %s", e)
        return None


async def load_last_data_result_with_retry(
    runner: Any,
    *,
    attempts: int = 3,
    delay_seconds: float = 0.15,
) -> Optional[Dict[str, Any]]:
    for attempt in range(attempts):
        result = await load_last_data_result(runner)
        if result:
            return result
        if attempt < attempts - 1:
            await asyncio.sleep(delay_seconds)
    return None


def normalize_rows_for_followup_save(parsed_tool_output: Any) -> Any:
    if isinstance(parsed_tool_output, list):
        return parsed_tool_output
    if isinstance(parsed_tool_output, dict):
        return parsed_tool_output
    return None


def latest_data_assistant_excerpt(
    history: List[Dict[str, str]],
    *,
    max_chars: int = 12000,
) -> str:
    for msg in reversed(history[:-1] or history):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) > max_chars:
            return content[:max_chars] + "\n... [对话展示过长已截断]"
        return content
    return ""


async def save_last_data_result_for_followups(
    runner: Any,
    tool_args: Dict[str, Any],
    parsed_tool_output: Any,
) -> None:
    normalized = normalize_rows_for_followup_save(parsed_tool_output)
    if not runner.conversation_id or normalized is None:
        return
    user_id = runner._current_user_id()
    if not user_id:
        return
    payload = {
        "sql": tool_args.get("sql") or tool_args.get("query"),
        "data_source": tool_args.get("data_source"),
        "dataset_name": tool_args.get("dataset_name"),
        "rows": normalized,
        "saved_at": datetime.now().isoformat(),
        "trace_id": runner.trace_id,
    }
    try:
        from app.services.ai.memory_service import memory_service

        await memory_service.set_last_data_result(user_id, runner.conversation_id, payload)
        state = runner._last_run_state
        if state is not None:
            state.followup_data_saved = True
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to save last data result: %s", e)
