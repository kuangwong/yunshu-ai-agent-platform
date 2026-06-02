import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.dispatcher import AgentDispatcher
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.intent_service import IntentType
from app.services.ai.turn_classifier import TurnClassification, TurnType


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_dispatcher_routes_data_capable_agent_to_data_executor_even_if_shared_turn_is_general():
    """Dispatcher 只按 agent 能力选 executor，不用 ChatBI 轮次分类决定是否进入 DataExecutor。"""
    config = ChatConfig(
        agent_id="sys-agent-chatbi",
        agent_name="chat-bi",
        agent_version=None,
        model_name="test-model",
        temperature=0.0,
        system_prompt="ChatBI",
        tools=["get_dataset_schema", "execute_sql_query"],
        capabilities=["data_query"],
        engine_type="LOCAL",
    )
    shared_turn = (
        TurnClassification(
            turn_type=TurnType.GENERAL,
            reasoning="外部粗分类不应决定 ChatBI 内部执行器",
            intent=IntentType.GENERAL,
        ),
        None,
        0.0,
    )

    executor = await AgentDispatcher.dispatch(
        config,
        user_query="分析一下",
        messages=[{"role": "user", "content": "分析一下"}],
        trace_id="trace-dispatch-boundary",
        trace_buffer=[],
        shared_turn=shared_turn,
    )

    assert isinstance(executor, DataQueryExecutor)
    assert not hasattr(executor, "turn_classification")
