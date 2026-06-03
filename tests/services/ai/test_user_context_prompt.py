import pytest

from app.services.ai.agent_service import AgentService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_user_context_prompt_uses_quick_buttons_conditionally():
    msg = await AgentService()._build_user_context_msg({"user_name": "tester"})

    content = msg["content"]
    assert "Active User Profile & Etiquette" in content
    assert "Smart Addressing" in content
