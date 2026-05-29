from typing import Dict, Any, Optional, List
import time
import logging
from app.services.ai.tools.data_api import get_dataset_schema, execute_sql_query
from app.services.ai.tools.dashboard_tools import update_dashboard_context
from app.services.ai.tools.generic_api import GenericApiToolFactory
from app.services.ai.tools.mcp_factory import McpToolFactory
from app.services.ai.tools.system_tools import system_http_request, SYSTEM_IMPLICIT_TOOLS
from app.services.ai.tools.knowledge_tool import search_knowledge_base
from app.services.ai.tools.task_manager_tools import (
    create_recurring_task, get_my_tasks, cancel_task, 
    start_task, pause_task, run_task_manually
)
from app.services.ai.tools.notification_tools import send_dingtalk_message, send_email
# Import Jira Tools
from app.services.ai.tools.jira_tools import JiraSearchTool, JiraCreateIssueTool, JiraGetProjectsTool
from app.services.ai.tools.system_executive_tools import (
    read_local_file, write_local_file, execute_system_command, manage_system_process,
    create_skills, list_available_skills, read_skill_instruction
)
from app.services.ai.tools.advanced_auxiliary_tools import (
    sqlite_scratchpad, directory_tree_navigator, web_renderer_and_snapshot, code_syntax_linter,
    fetch_static_web_url, web_search_baidu
)
from app.services.ai.tools.memory_ltm_tools import (
    update_user_preference, fetch_user_long_term_memory
)
from app.services.ai.tools.memory_search_tool import memory_search
from app.models.tool import SysApiTool
from app.models.mcp import McpToolCache
from app.core.orm import AsyncSessionLocal
from sqlalchemy import select

logger = logging.getLogger(__name__)

class ToolRegistry:
    """
    Registry for managing available tools for agents.
    Allows mapping string identifiers (from DB config) to actual LangChain tool objects.
    """
    
    # Core tools available by default or for recovery
    DEFAULT_TOOL_SET = ["get_dataset_schema", "execute_sql_query"]

    # Instantiate complex tools once
    _jira_search = JiraSearchTool()
    _jira_create = JiraCreateIssueTool()
    _jira_get_projects = JiraGetProjectsTool()
    _dingtalk_tool = send_dingtalk_message()
    _email_tool = send_email()

    _registry: Dict[str, Any] = {
        "get_dataset_schema": get_dataset_schema,
        "execute_sql_query": execute_sql_query,
        "update_dashboard_context": update_dashboard_context,
        "system_http_request": system_http_request,
        "search_knowledge_base": search_knowledge_base,
        # Register Task Manager Tools
        "create_recurring_task": create_recurring_task,
        "get_my_tasks": get_my_tasks,
        "cancel_task": cancel_task,
        "start_task": start_task,
        "pause_task": pause_task,
        "run_task_manually": run_task_manually,
        "send_dingtalk_message": _dingtalk_tool,
        "send_email": _email_tool,
        # Register Jira Tools
        "jira_search": _jira_search,
        "jira_create_issue": _jira_create,
        "jira_get_projects": _jira_get_projects,
        # Register System Executive Tools
        "read_local_file": read_local_file,
        "write_local_file": write_local_file,
        "execute_system_command": execute_system_command,
        "manage_system_process": manage_system_process,
        "create_skills": create_skills,
        "list_available_skills": list_available_skills,
        "read_skill_instruction": read_skill_instruction,
        # Register Advanced Auxiliary Tools
        "sqlite_scratchpad": sqlite_scratchpad,
        "directory_tree_navigator": directory_tree_navigator,
        "web_renderer_and_snapshot": web_renderer_and_snapshot,
        "code_syntax_linter": code_syntax_linter,
        "fetch_static_web_url": fetch_static_web_url,
        "web_search_baidu": web_search_baidu,
        # Register Memory LTM Tools
        "update_user_preference": update_user_preference,
        "fetch_user_long_term_memory": fetch_user_long_term_memory,
        "memory_search": memory_search,
    }

    # Cache for DB Tools
    _db_tool_cache: Dict[str, Any] = {}
    _db_tool_cache_ttl = 60.0 # 60 seconds
    _db_tool_ids_fetched_at: Dict[str, float] = {}

    @classmethod
    async def get_tool(cls, name: str) -> Optional[Any]:
        """Retrieve a tool by its name (Async)."""
        # 1. Check static registry
        if name in cls._registry:
            return cls._registry[name]

        # 2. Check Memory Cache
        now = time.time()
        if name in cls._db_tool_cache:
            last_fetched = cls._db_tool_ids_fetched_at.get(name, 0)
            if now - last_fetched < cls._db_tool_cache_ttl:
                return cls._db_tool_cache[name]

        # 3. Check DB for Generic or MCP Tools
        try:
            async with AsyncSessionLocal() as session:
                # A. Check MCP Tools first
                mcp_stmt = select(McpToolCache).where(
                    McpToolCache.tool_name == name, 
                    McpToolCache.is_published == True
                )
                mcp_res = await session.execute(mcp_stmt)
                mcp_config = mcp_res.scalar_one_or_none()
                
                if mcp_config:
                    tool_instance = McpToolFactory.create_tool(mcp_config)
                    cls._db_tool_cache[name] = tool_instance
                    cls._db_tool_ids_fetched_at[name] = time.time()
                    return tool_instance

                # B. Check Generic API Tools
                stmt = select(SysApiTool).where(SysApiTool.name == name, SysApiTool.is_active == True)
                gen_res = await session.execute(stmt)
                tool_config = gen_res.scalar_one_or_none()
                
                if tool_config:
                    tool_instance = GenericApiToolFactory.create_tool(tool_config)
                    cls._db_tool_cache[name] = tool_instance
                    cls._db_tool_ids_fetched_at[name] = time.time()
                    return tool_instance
        except Exception as e:
            logger.error(f"Error loading tool {name} from DB: {e}")
            pass
            
        return None

    @classmethod
    async def get_tools(cls, tool_configs: List[Any]) -> list[Any]:
        """
        Retrieve a list of tools, filtering out unknown ones (Async).
        Supports tool_configs as List[str] (legacy) or List[ToolConfigItem] (runtime config).
        """
        from app.schemas.agent import ToolConfigItem
        
        tools = []
        for item in tool_configs:
            name = ""
            config_item: Optional[ToolConfigItem] = None
            
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name", "")
                try:
                    config_item = ToolConfigItem(**item)
                except Exception as e:
                    logger.warning(f"Failed to parse tool config dict: {e}")
            elif hasattr(item, "name"):
                name = getattr(item, "name")
                config_item = item
            
            if not name:
                continue

            tool = await cls.get_tool(name)
            if not tool:
                continue

            # Apply Runtime Configuration if present (Including engine_config_override for Webhooks)
            if config_item:
                 tool = await cls._configure_tool_runtime(tool, config_item)
            
            tools.append(tool)
        return tools

    @classmethod
    async def _configure_tool_runtime(cls, tool: Any, config: Any) -> Any:
        """
        Injects runtime configuration (model, temperature) into a tool instance.
        """
        try:
            # 1. Capture original metadata BEFORE copy
            # Handle both Pydantic fields and instance attributes
            orig_name = getattr(tool, "name", None)
            orig_desc = getattr(tool, "description", None)
            
            # If description is missing on instance, try class attribute
            if not orig_desc and hasattr(tool, "__class__"):
                orig_desc = getattr(tool.__class__, "description", None)

            # 2. Perform Shallow Copy
            # We use copy.copy() as it's the most generic way to clone python objects
            # including Pydantic models (it calls __copy__)
            import copy
            tool_copy = copy.copy(tool)
            
            # 3. Restore Metadata (Force Overwrite)
            # This is critical because Pydantic's copy might reset fields to defaults
            if orig_name: 
                tool_copy.name = orig_name
            
            # Ensure description is never None
            if orig_desc:
                tool_copy.description = orig_desc
            else:
                tool_copy.description = f"Tool: {orig_name or 'unknown'}"

            # 4. Inject Runtime Config
            # We attach it as a hidden attribute to avoid Pydantic validation errors
            # if extra fields are forbidden
            object.__setattr__(tool_copy, "_runtime_config", config)
            
            return tool_copy
        except Exception as e:
            logger.error(f"Failed to configure tool runtime: {e}")
            # Fallback: return original tool if copying fails
            return tool

    @classmethod
    def register(cls, name: str, tool: Any):
        """Dynamically register a new tool (In-Memory)."""
        cls._registry[name] = tool

    @classmethod
    def get_system_implicit_tools(cls) -> List[Any]:
        """
        Get the list of system implicit tools that should be available to all agents
        but not necessarily visible in the configuration.
        """
        return SYSTEM_IMPLICIT_TOOLS + [
            update_user_preference,
            fetch_user_long_term_memory,
            memory_search,
            create_skills,
            list_available_skills,
            read_skill_instruction,
            system_http_request,
            fetch_static_web_url,
            web_renderer_and_snapshot,
            web_search_baidu,
        ]
