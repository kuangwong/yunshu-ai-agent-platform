import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage

from app.services.ai.executors.chat_executor import GeneralChatExecutor
from app.services.ai.intent_service import IntentResponse, IntentType
from app.services.ai.turn_classifier import TurnClassification, TurnType, attach_turn_classification
from app.services.ai.turn_classifier import (
    should_inject_ltm,
    should_inject_memory_recall_hint,
    should_inject_user_context,
    should_run_active_memory_preload,
)
from app.schemas.agent import ChatConfig


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.fixture
def chat_config():
    return ChatConfig(
        agent_id="test-agent-id",
        agent_name="TestAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a test agent.",
        tools=["search_knowledge_base"],
    )


@pytest.mark.asyncio
async def test_knowledge_turn_forces_search_before_answer(chat_config):
    executor = GeneralChatExecutor(
        config=chat_config, trace_id="test-knowledge", trace_buffer=[], conversation_id="c1"
    )
    classification = TurnClassification(
        turn_type=TurnType.KNOWLEDGE,
        reasoning="SOP 问答",
        requires_knowledge_search=True,
        intent=IntentType.KNOWLEDGE_BASE,
    )
    attach_turn_classification(executor, classification)
    assert executor._requires_knowledge_search is True

    mock_tool = AsyncMock()
    mock_tool.name = "search_knowledge_base"
    mock_tool.ainvoke.return_value = "检索结果"

    direct_answer = AIMessage(content="这是编造的流程")
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{
            "name": "search_knowledge_base",
            "args": {"query": "流程"},
            "id": "call_kb_1",
        }],
    )

    mock_llm = MagicMock()
    mock_llm.model_name = "gpt-4o"
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    call_idx = {"n": 0}

    async def astream_side_effect(messages):
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            yield direct_answer
        else:
            yield tool_call_msg

    mock_llm.astream = astream_side_effect

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock, return_value=mock_llm), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock, return_value=[mock_tool]), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock, return_value="5"), \
         patch("app.services.memory_config_service.MemoryConfigService.get_bool", new_callable=AsyncMock, return_value=False):
        history = [{"role": "user", "content": "高温告警处理流程是什么"}]
        logs = []
        async for chunk in executor.execute(history):
            if chunk.get("type") == "log":
                logs.append(chunk)

    assert any("强制知识库检索" in l.get("title", "") for l in logs)
    assert mock_tool.ainvoke.called


def test_injection_profile_for_data_turns():
    assert should_inject_ltm(TurnType.K1_NEW_QUERY) is False
    assert should_inject_memory_recall_hint(TurnType.K1_NEW_QUERY) is False
    assert should_run_active_memory_preload(TurnType.K2_REUSE_RESULT) is False
    assert should_inject_ltm(TurnType.K3_CONTEXT_ACTION) is True
    assert should_inject_memory_recall_hint(TurnType.KNOWLEDGE) is False
    assert should_inject_user_context(TurnType.K1_NEW_QUERY) is False
    assert should_inject_user_context(TurnType.GENERAL) is True
