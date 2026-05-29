# 智能路由与编排 (Orchestration & Routing)

## Purpose
`RouterService` 是多智能体系统的核心中枢，负责根据用户意图和对话上下文，将请求分发给最合适的智能体（Agent）。

## Requirements
### Requirement: Intelligent Routing Strategy
The system SHALL route user queries to the most appropriate agent based on intent and context.

#### Scenario: Context Aware Routing
- **WHEN** user sends a query
- **THEN** system analyzes input using LLM
- **AND** system considers recent conversation history (N turns)
- **AND** system checks agent `description` and `capabilities`
- **AND** system returns target agent ID with confidence score

#### Scenario: Fallback Mechanism
- **WHEN** routing confidence is below threshold (e.g., 0.5)
- **THEN** system falls back to `general-chat` or default logic

### Requirement: Implementation Details
The system MUST implement the following data flow and API definitions to support routing logic.

#### Data Flow
1. **输入**: 用户 Query + 对话历史 (Conversation History)。
2. **处理**:
   - 构建 Prompt，注入 Agent 列表和历史摘要。
   - 调用 LLM 获取 JSON 格式的路由结果（包含 `agent_name` 和 `confidence`）。
3. **输出**: 目标 Agent 的标识符。

#### API Definitions
**RouterService.route_query**
```python
async def route_query(self, user_input: str, history: Optional[List[dict]] = None) -> Optional[RouteResult]
```
- **user_input**: 用户当前的文本输入。
- **history**: 对话历史列表，格式为 `[{'role': 'user', 'content': '...'}, ...]`。用于增强语义理解。

#### Configuration
- **System Prompt**: 内置于代码 `RouterService.DEFAULT_SYSTEM_PROMPT`，定义了路由器的角色和规则。不再从数据库 `system_configs` 读取，也不在"提示词管理"中暴露，避免运营误改导致路由失准。
- **Agent Metadata**: 动态从 `agents` 表加载，作为 Prompt 的上下文。

## History
- **2026-01-05**: 增加上下文感知能力 (`history` 参数)，优化多轮对话路由准确率。
