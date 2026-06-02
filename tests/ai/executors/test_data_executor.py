import pytest
import json
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, HumanMessage
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.intent_service import IntentType
from app.services.ai.data_query_turn_classifier import DataQueryTurnClassification, DataQueryTurnType
from app.schemas.agent import ChatConfig

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override the global fixture to avoid real DB/Redis connection in unit tests."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.fixture(scope="function", autouse=True)
def default_data_query_turn_classification():
    classification = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning="测试默认：新数据查询",
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=False,
        intent=IntentType.DATA_QUERY,
    )
    with patch(
        "app.services.ai.executors.data_executor.resolve_data_query_turn_classification",
        AsyncMock(return_value=(classification, None, 0.0)),
    ) as mock_resolve:
        yield mock_resolve

class MockLLM:
    def __init__(self, responses):
        self.responses = responses # List of AIMessage
        self.call_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            return res
        return AIMessage(content="Final Answer")

    async def astream(self, messages):
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            yield res
        else:
            yield AIMessage(content="Final Response")

@pytest.fixture
def data_config():
    return ChatConfig(
        agent_id="data-agent-id",
        agent_name="DataQueryAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a data assistant. Use {dataset_menu} to see available tables.",
        tools=["get_dataset_schema", "execute_sql_query"]
    )

@pytest.fixture
def mock_tool_schema():
    tool = AsyncMock()
    tool.name = "get_dataset_schema"
    tool.ainvoke.return_value = {"tables": ["table1"]}
    return tool

@pytest.fixture
def mock_tool_sql():
    tool = AsyncMock()
    tool.name = "execute_sql_query"
    tool.ainvoke.return_value = [{"count": 10}]
    return tool

# --- Tests ---

@pytest.mark.asyncio
async def test_data_executor_full_flow(data_config, mock_tool_schema, mock_tool_sql):
    """测试完整的 DataQueryExecutor 流程: 获取 Schema -> 执行 SQL -> 回答"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-data-1", trace_buffer=[])
    
    # Sequence of LLM actions
    msg_1_schema = AIMessage(
        content="I need to check the schema.",
        tool_calls=[{"name": "get_dataset_schema", "args": {"keyword": "test"}, "id": "call_schema"}]
    )
    msg_2_sql = AIMessage(
        content="Schema found. Now running query.",
        tool_calls=[{"name": "execute_sql_query", "args": {"query": "SELECT count() FROM table1"}, "id": "call_sql"}]
    )
    msg_3_final = AIMessage(content="The count is 10.")
    
    mock_llm = MockLLM([msg_1_schema, msg_2_sql, msg_3_final])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock) as mock_record:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = "Table1: User Data"
        mock_get_tools.return_value = [mock_tool_schema, mock_tool_sql]
        
        # ToolRegistry.get_tool needs to return the right tool based on name
        def side_effect_get_tool(name):
            if name == "get_dataset_schema": return mock_tool_schema
            if name == "execute_sql_query": return mock_tool_sql
            return None
        mock_get_tool.side_effect = side_effect_get_tool
        
        mock_config_get.return_value = "5"
        
        history = [{"role": "user", "content": "How many users?"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        # Verify events
        log_events = [e for e in events if e.get("type") == "log"]
        log_titles = [e["title"] for e in log_events]
        log_details = [e.get("details", "") for e in log_events]

        assert any("检索数据集定义" in t for t in log_titles)
        assert any("执行 SQL 查询" in t for t in log_titles)
        
        # Verify the new specific details we added
        assert any("检索关键词" in d for d in log_details), "Logs should contain search keywords"
        assert any("命中 1 行数据" in d for d in log_details), "SQL logs should contain result count preview"
        
        # Verify content
        content = "".join([e["content"] for e in events if "content" in e])
        assert "Final Response" in content or "The count is 10." in content

@pytest.mark.asyncio
async def test_data_executor_aborts_when_metadata_unavailable(data_config, mock_tool_schema, mock_tool_sql):
    """元数据服务不可用（502）时应硬终止：绝不调用 execute_sql_query，并返回明确失败提示。"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-md-unavailable", trace_buffer=[])

    # get_dataset_schema 返回「不可用」哨兵文案
    mock_tool_schema.ainvoke.return_value = (
        "[元数据服务不可用] 元数据检索服务（RAGFlow）当前无法访问，暂时无法获取数据集结构信息。"
        "\n（错误详情：RAGFlow HTTP Error 502: ）"
    )

    msg_1_schema = AIMessage(
        content="Checking schema.",
        tool_calls=[{"name": "get_dataset_schema", "args": {"keywords": "用户表"}, "id": "call_schema"}]
    )
    # 即便模型“想”执行 SQL，执行器也应在拿到不可用结果后提前终止，永不触达此步
    msg_2_sql = AIMessage(
        content="I'll just query anyway.",
        tool_calls=[{"name": "execute_sql_query", "args": {"sql": "SELECT * FROM 测试表", "data_source": "x", "dataset_name": "y"}, "id": "call_sql"}]
    )
    mock_llm = MockLLM([msg_1_schema, msg_2_sql])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search:

        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_schema, mock_tool_sql]

        def side_effect_get_tool(name):
            if name == "get_dataset_schema": return mock_tool_schema
            if name == "execute_sql_query": return mock_tool_sql
            return None
        mock_get_tool.side_effect = side_effect_get_tool
        mock_config_get.return_value = "5"

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "用户表有多少数据"}]):
            events.append(chunk)

    # 关键断言：SQL 工具从未被调用（没有臆造查询）
    mock_tool_sql.ainvoke.assert_not_called()
    # 终止提示已展示
    titles = [e.get("title", "") for e in events if e.get("type") == "log"]
    assert any("终止" in t for t in titles)
    # 最终内容是「服务不可用」而非编造结论
    content = "".join(e["content"] for e in events if "content" in e)
    assert "不可用" in content


@pytest.mark.asyncio
async def test_data_executor_get_dataset_schema_truncation(data_config, mock_tool_schema):
    """测试 get_dataset_schema 工具输出的精简（隐藏 columns）和安全截断逻辑"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-truncation", trace_buffer=[])
    
    # 模拟一个巨大的元数据输出，包含 columns 标记
    massive_columns = "id: int\nname: string\n" * 1000
    huge_output = f"--- Source: test_table ---\ntable_name: test\ncolumns:\n{massive_columns}\nrelationships: []"
    mock_tool_schema.ainvoke.return_value = huge_output
    
    msg_schema = AIMessage(
        content="Checking schema.",
        tool_calls=[{"name": "get_dataset_schema", "args": {"keywords": "test"}, "id": "call_1"}]
    )
    mock_llm = MockLLM([msg_schema])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_schema]
        mock_get_tool.return_value = mock_tool_schema
        mock_config_get.return_value = "5"
        
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "query"}]):
            events.append(chunk)

        log_event = next(e for e in events if e.get("id") == "call_1" and "查询完成" in e.get("title", ""))
        details = log_event["details"]

        # 验证 columns 是否被精简
        assert "[已隐藏具体字段定义，仅 AI 可见]" in details
        assert "id: int" not in details, "Columns should be removed from logs"

        # 验证 relationships 标记被保留（正则截断点测试）
        assert "relationships: []" in details

        # --- 第二部分：测试安全截断 ---
        # 模拟一个即使精简后依然巨大的输出（例如上百个数据集描述）
        huge_metadata = "--- Dataset: Sample ---\nDescription: " + ("Very long description. " * 1000)
        mock_tool_schema.ainvoke.return_value = huge_metadata

        mock_llm.call_count = 0 # 重置 MockLLM

        # 再次进入 patch 环境进行第二次执行
        with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm_2, \
             patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu_2, \
             patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools_2, \
             patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool_2, \
             patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get_2, \
             patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search_2:

            mock_search_2.return_value = []
            mock_get_llm_2.return_value = mock_llm
            mock_get_menu_2.return_value = ""
            mock_get_tools_2.return_value = [mock_tool_schema]
            mock_get_tool_2.return_value = mock_tool_schema
            mock_config_get_2.return_value = "5"

            events = []
            async for chunk in executor.execute([{"role": "user", "content": "query"}]):
                events.append(chunk)

            log_event = next(e for e in events if e.get("id") == "call_1" and "查询完成" in e.get("title", ""))
            details = log_event["details"]
            
            # 验证安全截断逻辑 (15000 chars)
            assert len(details) <= 16000 # 15000 + 截断提示的长度
            assert "... [内容过长已截断" in details
@pytest.mark.asyncio
async def test_data_executor_xml_parsing_and_correction(data_config, mock_tool_sql):
    """测试 XML 格式解析及其失败后的自修正逻辑"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-data-xml", trace_buffer=[])
    
    # 1. LLM outputs malformed/unparsed XML (or just XML as string)
    xml_content = "<function_calls><invoke name=\"execute_sql_query\"><parameter name=\"query\">SELECT 1</parameter></invoke></function_calls>"
    msg_1_xml = AIMessage(content=xml_content)
    # Note: If tool_calls is empty but content has <function_calls>, it triggers parsing in the executor.
    # If parsing fails or we want to test the Nudge for malformed XML:
    
    msg_final = AIMessage(content="XML processed.")
    
    mock_llm = MockLLM([msg_1_xml, msg_final])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock) as mock_record:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_sql]
        mock_get_tool.return_value = mock_tool_sql
        mock_config_get.return_value = "5"
        
        history = [{"role": "user", "content": "Run query"}]
        async for _ in executor.execute(history):
            pass
            
        # Verify XML was parsed
        mock_tool_sql.ainvoke.assert_called_once_with({"query": "SELECT 1"})

@pytest.mark.asyncio
async def test_data_executor_procrastination_nudge(data_config, mock_tool_schema):
    """测试“拖延检测”逻辑: 当模型在第一步只说话不行动时给予提示"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-data-nudge", trace_buffer=[])
    
    # Step 1: Model just talks about searching (must be > 50 chars to trigger new nudge)
    msg_1_talking = AIMessage(content="好的，为了能够为您提供准确的数据查询服务，我需要先调用工具查询一下数据库中相关表的 schema 定义，请您稍等片刻，我马上为您处理。")
    # Step 2: After nudge, model calls tool
    msg_2_tool = AIMessage(content="", tool_calls=[{"name": "get_dataset_schema", "args": {"keyword": "test"}, "id": "c1"}])
    msg_final = AIMessage(content="Final.")
    
    mock_llm = MockLLM([msg_1_talking, msg_2_tool, msg_final])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock) as mock_record:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_schema]
        mock_get_tool.return_value = mock_tool_schema
        mock_config_get.return_value = "5"
        
        history = [{"role": "user", "content": "Search something"}]
        async for _ in executor.execute(history):
            pass
            
        # Verify it took 3 iterations of LLM (msg_1, msg_2, msg_3)
        assert mock_llm.call_count == 3
        mock_tool_schema.ainvoke.assert_called_once()

@pytest.mark.asyncio
async def test_data_executor_dry_run(data_config, mock_tool_sql):
    """测试 Dry Run 模式下拦截 SQL 执行"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-dry-run", trace_buffer=[])
    executor.debug_options["dry_run"] = True
    
    msg_sql = AIMessage(content="", tool_calls=[{"name": "execute_sql_query", "args": {"query": "DROP TABLE users"}, "id": "c1"}])
    mock_llm = MockLLM([msg_sql])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock) as mock_record:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_sql]
        mock_get_tool.return_value = mock_tool_sql
        mock_config_get.return_value = "5"
        
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "run dangerous query"}]):
            events.append(chunk)
            
        # Tool should NOT be invoked
        mock_tool_sql.ainvoke.assert_not_called()
        
        # Log should show dry run result
        # Note: We filter for the log that contains the dry run message, as there might be multiple logs (e.g. permission logs)
        dry_run_logs = [e for e in events if e.get("type") == "log" and e.get("status") == "success" and "[DRY RUN MODE]" in (e.get("details") or "")]
        assert len(dry_run_logs) > 0, f"Dry run log not found in events: {events}"

@pytest.mark.asyncio
async def test_data_executor_self_healing_sql_error(data_config, mock_tool_sql):
    """测试自愈反馈: SQL 字段名错误时给予引导"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-healing", trace_buffer=[])
    
    # 1. Test the internal feedback generation logic directly
    raw_error = "[TOOL_ERROR] Unknown column 'usr_name' in 'table1'"
    feedback = executor._get_self_healing_feedback("execute_sql_query", raw_error)
    assert "[规划修正] 字段名错误" in feedback

    # 2. Test the flow
    msg_sql = AIMessage(content="", tool_calls=[{"name": "execute_sql_query", "args": {"query": "SELECT usr_name FROM table1"}, "id": "c1"}])
    mock_llm = MockLLM([msg_sql])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock) as mock_record:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_sql]
        mock_get_tool.return_value = mock_tool_sql
        mock_config_get.return_value = "5"
        
        # Tool returns error
        mock_tool_sql.ainvoke.return_value = raw_error
        
        async for _ in executor.execute([{"role": "user", "content": "query"}]):
            pass
            
        # Verify trace buffer contains the raw error
        # Inspect trace buffer
        print(f"DEBUG Trace Buffer: {[step.tool_output for step in executor.trace_buffer if step.event_type == 'tool_call']}")
        assert any("Unknown column 'usr_name'" in str(step.tool_output) for step in executor.trace_buffer if step.event_type == "tool_call")

@pytest.mark.asyncio
async def test_data_executor_critical_error_stop(data_config, mock_tool_sql):
    """测试关键系统错误导致立即终止循环"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-critical", trace_buffer=[])
    
    # Critical error: Authentication failed
    mock_tool_sql.ainvoke.return_value = "[TOOL_ERROR] CRITICAL: Authentication failed for user 'admin'"
    
    msg_sql = AIMessage(content="", tool_calls=[{"name": "execute_sql_query", "args": {"query": "SELECT 1"}, "id": "c1"}])
    # Even if we have more messages, it should stop
    mock_llm = MockLLM([msg_sql, AIMessage(content="Should not reach here")])
    
    # Mock synthesis LLM to avoid second call hitting the main MockLLM (if that's what happens)
    mock_syn = MagicMock()
    async def mock_astream_syn(*args, **kwargs):
        yield AIMessage(content="Synthesis")
    mock_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", new_callable=AsyncMock) as mock_get_syn, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock) as mock_record:
         
        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_syn.return_value = mock_syn
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_sql]
        mock_get_tool.return_value = mock_tool_sql
        mock_config_get.return_value = "5"
        
        async for _ in executor.execute([{"role": "user", "content": "query"}]):
            pass
            
        # Only 1 call to LLM (Orchestrator) before stopping
        assert mock_llm.call_count == 1
        assert any(step.status == "error" and step.error_message.find("Authentication failed") != -1 for step in executor.trace_buffer)

@pytest.mark.asyncio
async def test_data_executor_retries_after_recoverable_sql_tool_error(data_config, mock_tool_sql):
    """SQL 安全/语法类工具错误应反馈给模型继续修正，而不是直接进入总结。"""
    executor = DataQueryExecutor(config=data_config, trace_id="test-recoverable-sql-error", trace_buffer=[])

    msg_bad_sql = AIMessage(
        content="",
        tool_calls=[{"name": "execute_sql_query", "args": {"query": "WITH t AS (SELECT 1) SELECT * FROM t"}, "id": "c1"}],
    )
    msg_fixed_sql = AIMessage(
        content="",
        tool_calls=[{"name": "execute_sql_query", "args": {"query": "SELECT 1"}, "id": "c2"}],
    )
    mock_llm = MockLLM([msg_bad_sql, msg_fixed_sql])

    mock_syn = MagicMock()
    async def mock_astream_syn(*args, **kwargs):
        yield AIMessage(content="Synthesis")
    mock_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", new_callable=AsyncMock) as mock_get_syn, \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", new_callable=AsyncMock) as mock_get_menu, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tool", new_callable=AsyncMock) as mock_get_tool, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", new_callable=AsyncMock) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService.record_usage", new_callable=AsyncMock):

        mock_search.return_value = []
        mock_get_llm.return_value = mock_llm
        mock_get_syn.return_value = mock_syn
        mock_get_menu.return_value = ""
        mock_get_tools.return_value = [mock_tool_sql]
        mock_get_tool.return_value = mock_tool_sql
        mock_config_get.return_value = "5"
        mock_tool_sql.ainvoke.side_effect = [
            "[TOOL_ERROR] 安全策略违规：禁止执行 'WITH' 指令。本地模式仅允许执行只读 SELECT 查询",
            [{"ok": 1}],
        ]

        async for _ in executor.execute([{"role": "user", "content": "query"}]):
            pass

        assert mock_llm.call_count >= 2
        assert mock_tool_sql.ainvoke.call_count == 2
        tool_steps = [step for step in executor.trace_buffer if step.event_type == "tool_call"]
        assert [step.status for step in tool_steps] == ["error", "success"]


def test_analyze_result_dataset_not_found_is_error(data_config):
    executor = DataQueryExecutor(config=data_config, trace_id="test-analyze-dataset", trace_buffer=[])
    err = "Error: Dataset 'user_list' not found. Please verify the dataset name."
    status, msg, critical = executor._analyze_result(err)
    assert status == "error"
    assert "not found" in (msg or "").lower()
    assert critical is False


def test_analyze_result_validation_failed_is_error(data_config):
    executor = DataQueryExecutor(config=data_config, trace_id="test-analyze-validation", trace_buffer=[])
    status, _, _ = executor._analyze_result("[Validation Failed] SQL语法错误: foo")
    assert status == "error"


def test_analyze_result_json_rows_still_success(data_config):
    executor = DataQueryExecutor(config=data_config, trace_id="test-analyze-json", trace_buffer=[])
    status, _, _ = executor._analyze_result('[{"id": 1}]')
    assert status == "success"


@pytest.mark.parametrize("err_text", [
    "Error: Dataset 'user_list' not found. Please verify the dataset name.",
    "[Validation Failed] SQL语法错误: foo",
    "[Permission Denied] 无权访问表 't'",
    "[Security Error] Failed to apply data permissions: x",
    "[TOOL_ERROR] 本地执行 SQL 失败，错误信息: boom",
    "[Tool Error] Failed to retrieve metadata: boom",
    "[RAG Error] API returned 502: bad gateway",
    "[System Config Error] RAGFlow API URL or API Key is missing.",
    "Empty SQL query.",
    "Only read-only queries (SELECT, EXPLAIN, SHOW, DESCRIBE) are allowed.",
    "Multi-statement queries are prohibited.",
    "Write/DDL operations are not allowed.",
    "SQL parse failed: unexpected token",
    "MySQL SQL Syntax Error: bad syntax",
    "SQL Validation Failed: no select",
])
def test_analyze_result_common_sql_and_tool_errors(data_config, err_text):
    executor = DataQueryExecutor(config=data_config, trace_id="test-analyze-errors", trace_buffer=[])
    status, _, _ = executor._analyze_result(err_text)
    assert status == "error", err_text

