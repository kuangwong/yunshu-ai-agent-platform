import pytest
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.router_service import RouterService, RouteResult

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override infrastructure initialization to avoid real DB connections."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield

@pytest.fixture
def mock_agents_metadata():
    return [
        {
            "id": "agent-chatbi",
            "name": "ChatBI",
            "description": "SQL Data Analyst",
            "capabilities": ["text-to-sql", "chart"]
        },
        {
            "id": "agent-rag",
            "name": "KnowledgeBase",
            "description": "Document Retrieval",
            "capabilities": ["rag", "qa"]
        },
        {
            "id": "agent-general",
            "name": "general-chat",
            "description": "General Assistant",
            "capabilities": ["chat"]
        }
    ]

class MockLLMResponse:
    def __init__(self, content):
        self.content = content

# --- Tests ---

@pytest.mark.asyncio
async def test_router_service_fetch_agents_mock(mock_agents_metadata):
    """测试从 DB 获取 Agents 列表的 Mock 逻辑"""
    service = RouterService()
    
    # Mock _fetch_agents_from_db directly to isolate from SQL logic in this unit test
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_agents_metadata
        
        agents = await service._fetch_agents_from_db()
        assert len(agents) == 3
        assert agents[0]["name"] == "ChatBI"

@pytest.mark.asyncio
async def test_route_query_high_confidence(mock_agents_metadata):
    """测试高置信度路由: 明确匹配 ChatBI"""
    service = RouterService()
    
    # Mock JSON response from LLM using the new 'thought' field
    llm_resp_content = json.dumps({
        "thought": "Query asks for data table.",
        "agent_name": "ChatBI",
        "confidence": 0.95
    })
    
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MockLLMResponse(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
         
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_config.return_value = "System Prompt"
        
        result = await service.route_query("Show me user count")
        
        assert isinstance(result, RouteResult)
        assert result.agent_id == "agent-chatbi"
        assert result.confidence == 0.95
        assert result.reasoning == "Query asks for data table."
        assert result.turn_labels == []
        assert result.relation_to_previous == "unknown"
        assert result.user_action_type == "unknown"


@pytest.mark.asyncio
async def test_route_query_returns_generic_turn_hints(mock_agents_metadata):
    """路由可输出通用会话标签，但它们只是 executor 可选择使用的 hint。"""
    service = RouterService()

    llm_resp_content = json.dumps({
        "thought": "This is a follow-up to the previous data answer.",
        "agent_name": "ChatBI",
        "secondary_agents": [],
        "confidence": 0.92,
        "turn_labels": ["continuation_followup", "business_related", "same_topic", "unknown_label"],
        "relation_to_previous": "followup",
        "user_action_type": "transform_context"
    })

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MockLLMResponse(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm:

        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm

        result = await service.route_query("把上面的结果画成柱状图")

    assert result.agent_id == "agent-chatbi"
    assert result.turn_labels == ["continuation_followup", "business_related", "same_topic"]
    assert result.relation_to_previous == "followup"
    assert result.user_action_type == "transform_context"

@pytest.mark.asyncio
async def test_route_query_low_confidence_fallback(mock_agents_metadata):
    """测试低置信度回退: 路由给 general-chat"""
    service = RouterService()
    
    # Confidence 0.4 < 0.6 threshold
    llm_resp_content = json.dumps({
        "thought": "Not sure.",
        "agent_name": "ChatBI",
        "confidence": 0.4
    })
    
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MockLLMResponse(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
         
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_config.return_value = "System Prompt"
        
        result = await service.route_query("Hello")
        
        # Should fallback to general-chat
        assert result.agent_id == "agent-general"
        assert "Low confidence" in result.reasoning
        assert "Not sure." in result.reasoning

@pytest.mark.asyncio
async def test_route_query_unknown_agent_fallback(mock_agents_metadata):
    """测试 LLM 返回未知 Agent 名称时的回退"""
    service = RouterService()
    
    llm_resp_content = json.dumps({
        "thought": "I made this up.",
        "agent_name": "SuperAgent", # Does not exist
        "confidence": 0.99
    })
    
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MockLLMResponse(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
         
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_config.return_value = "System Prompt"
        
        result = await service.route_query("Do magic")
        
        assert result.agent_id == "agent-general"
        assert "unknown agent" in result.reasoning

@pytest.mark.asyncio
async def test_router_caching(mock_agents_metadata):
    """测试 Agent 列表的缓存机制"""
    service = RouterService()
    service._cache_ttl = 60
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        
        mock_fetch.return_value = mock_agents_metadata
        # Ensure ainvoke returns an object with .content immediately (since it's awaited)
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.return_value = MockLLMResponse('{"thought": "test", "agent_name": "ChatBI", "confidence": 1.0}')
        mock_get_llm.return_value = mock_llm_instance
        
        mock_config.return_value = ""

        # First call: fetch from DB
        await service.route_query("Q1")
        assert mock_fetch.call_count == 1
        
        # Second call (immediate): use cache
        await service.route_query("Q2")
        assert mock_fetch.call_count == 1 # Should not increment
        
        # Invalidate cache
        service.invalidate_cache()
        
        # Third call: fetch again
        await service.route_query("Q3")
        assert mock_fetch.call_count == 2


@pytest.mark.asyncio
async def test_route_query_filters_candidates_by_user_permission(mock_agents_metadata):
    """路由候选应先按用户可访问智能体过滤，避免选到随后被拒绝的 agent。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Only allowed agent is suitable.",
        "agent_name": "KnowledgeBase",
        "confidence": 0.95
    })

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MockLLMResponse(llm_resp_content)
    permission_response = SimpleNamespace(
        roles=["user"],
        permissions=SimpleNamespace(agents=["agent-rag"])
    )

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_perms:

        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_config.return_value = RouterService.DEFAULT_SYSTEM_PROMPT
        mock_perms.return_value = permission_response

        result = await service.route_query("查文档", user_id=1001, is_admin=False)

    assert result.agent_id == "agent-rag"
    routed_messages = mock_llm.ainvoke.call_args[0][0]
    system_prompt = routed_messages[0].content
    assert "ID: KnowledgeBase" in system_prompt
    assert "UUID: agent-rag" in system_prompt
    assert "UUID: agent-chatbi" not in system_prompt
