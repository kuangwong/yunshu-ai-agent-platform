import logging
from typing import List, Dict, Any, Optional
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.turn_classifier import (
    SharedTurn,
    adapt_classification_for_agent,
    attach_turn_classification,
    resolve_turn_classification,
    resolve_turn_for_session,
    turn_type_label,
)
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.executors.chat_executor import GeneralChatExecutor
from app.services.ai.executors.rag_executor import RAGExecutor
from app.services.ai.executors.openclaw_executor import OpenClawExecutor

logger = logging.getLogger(__name__)

class AgentDispatcher:
    """
    Dispatches agent execution to the appropriate Executor based on configuration and intent.
    """

    @staticmethod
    async def dispatch(
        agent_config: ChatConfig,
        user_query: str,
        messages: List[Dict[str, str]],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Optional[Dict[str, Any]] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        shared_turn: Optional[SharedTurn] = None,
    ) -> BaseExecutor:
        """
        Determines and returns the correct Executor instance.
        shared_turn: 多智能体/会话级已算好的分类，避免重复意图 LLM。
        """
        
        # 1. External Engine Check
        if agent_config.engine_type == 'RAGFLOW':
            return RAGExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
        
        if agent_config.engine_type == 'OPENCLAW':
            return OpenClawExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)

        can_do_data = "data_query" in (agent_config.capabilities or [])

        # 2. 轮次分类（共享或独立解析）
        if shared_turn is not None:
            classification, intent_info, intent_elapsed_ms = shared_turn
            classification = adapt_classification_for_agent(classification, can_do_data=can_do_data)
        elif can_do_data:
            classification, intent_info, intent_elapsed_ms = await resolve_turn_classification(
                user_query,
                messages,
                can_do_data=True,
                user_info=user_info,
                conversation_id=conversation_id,
            )
        else:
            classification, intent_info, intent_elapsed_ms = await resolve_turn_for_session(
                user_query,
                messages,
                can_do_data=False,
                user_info=user_info,
                conversation_id=conversation_id,
            )
            classification = adapt_classification_for_agent(classification, can_do_data=False)

        logger.info(
            "[Dispatcher] turn=%s executor=%s skip_intent=%s agent=%s",
            turn_type_label(classification.turn_type),
            "DataQuery" if (can_do_data and classification.use_data_executor) else "GeneralChat",
            classification.skip_intent_llm,
            agent_config.agent_name,
        )

        if can_do_data and classification.use_data_executor:
            executor = DataQueryExecutor(
                agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id
            )
        else:
            executor = GeneralChatExecutor(
                agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id
            )

        attach_turn_classification(
            executor,
            classification,
            intent_info=intent_info,
            intent_elapsed_ms=intent_elapsed_ms,
        )
        return executor
