import logging
from typing import List, Dict, Any, Optional
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.turn_classifier import (
    SharedTurn,
    adapt_classification_for_agent,
    attach_turn_classification,
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

        # ChatBI/DataQueryExecutor owns its internal data-query request classification.
        # Dispatcher only chooses the executor based on agent capability.
        if can_do_data:
            logger.info(
                "[Dispatcher] executor=DataQuery agent=%s (data_query capability)",
                agent_config.agent_name,
            )
            return DataQueryExecutor(
                agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id
            )

        # 2. 非数据执行器保留现有粗分类，用于知识库护栏等非 ChatBI 流程。
        if shared_turn is not None:
            classification, intent_info, intent_elapsed_ms = shared_turn
            classification = adapt_classification_for_agent(classification, can_do_data=can_do_data)
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
            "GeneralChat",
            classification.skip_intent_llm,
            agent_config.agent_name,
        )

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
