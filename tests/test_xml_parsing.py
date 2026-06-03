import sys
import os
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai.executors.data_executor import DataQueryExecutor
from app.schemas.agent import ChatConfig

def test_xml_parsing():
    content = """我来帮您查询所有机房的列表。首先让我获取相关的元数据信息。

<function_calls>
<invoke name="get_dataset_schema">
<parameter name="query">机房 列表 机房信息</parameter>
</invoke>
</function_calls>"""

    # Mock config
    config = ChatConfig(
        agent_id="1",
        agent_name="test",
        model_name="test-model",
        temperature=0.0,
        system_prompt="test",
        tools=["get_dataset_schema"]
    )
    
    executor = DataQueryExecutor(config, trace_id="test-trace", trace_buffer=[])
    tool_calls = executor._parse_xml_tool_calls(content)
    
    print(f"Parsed Tool Calls: {json.dumps(tool_calls, indent=2, ensure_ascii=False)}")
    
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "get_dataset_schema"
    assert tool_calls[0]["args"]["query"] == "机房 列表 机房信息"
    assert tool_calls[0]["id"].startswith("call_")
    print("Test passed!")

if __name__ == "__main__":
    test_xml_parsing()
