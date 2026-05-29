from unittest.mock import PropertyMock, patch

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_skill_discovery_tools_list_and_read_skill(tmp_path):
    from app.services.ai.tools.system_executive_tools import (
        list_available_skills,
        read_skill_instruction,
    )

    skill_dir = tmp_path / "ops-helper"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: 运维辅助技能\n"
        "description: 处理运维排障流程\n"
        "---\n\n"
        "# 操作守则\n"
        "先定位影响范围，再给出处置步骤。\n",
        encoding="utf-8",
    )
    hidden_dir = tmp_path / ".draft"
    hidden_dir.mkdir()
    (hidden_dir / "SKILL.md").write_text("hidden", encoding="utf-8")

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        listing = list_available_skills.invoke({})
        assert "ops-helper" in listing
        assert "运维辅助技能" in listing
        assert "处理运维排障流程" in listing
        assert ".draft" not in listing

        instruction = read_skill_instruction.invoke({"skill_id": "ops-helper"})
        assert "操作守则" in instruction
        assert "先定位影响范围" in instruction

        bad = read_skill_instruction.invoke({"skill_id": "../ops-helper"})
        assert "非法技能 ID" in bad


def test_general_chat_implicit_tools_include_safe_skill_lookup():
    from app.services.ai.tools.registry import ToolRegistry

    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}

    assert "list_available_skills" in tool_names
    assert "read_skill_instruction" in tool_names
