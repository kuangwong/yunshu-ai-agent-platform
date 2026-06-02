import pytest

from app.services.ai.intent_service import (
    looks_like_meta_action,
    looks_like_skill_execution,
    looks_like_compound_query_with_viz,
    looks_like_pure_result_followup,
    looks_like_data_followup,
)
from app.services.ai.skill_resolver import _extract_skill_hints, _score_skill_match


pytestmark = pytest.mark.no_infrastructure


def test_extract_skill_hint_from_use_skill_query():
    hints = _extract_skill_hints("使用用户列表查询技能查询一次")
    assert "用户列表查询" in hints


def test_looks_like_skill_execution_not_meta_action():
    q = "使用用户列表查询技能查询一次"
    assert looks_like_skill_execution(q) is True
    assert looks_like_meta_action(q) is False


def test_score_skill_match_by_name():
    meta = {"id": "user-list-query", "name": "用户列表查询", "description": "查询用户列表"}
    assert _score_skill_match("用户列表查询", meta) >= 0.9


def test_compound_query_with_viz_is_not_pure_result_followup():
    q = "查询用户列表并可视化分析"
    assert looks_like_compound_query_with_viz(q) is True
    assert looks_like_pure_result_followup(q) is False
    assert looks_like_data_followup(q) is False


def test_user_list_viz_without_chaxun_still_new_query_compound():
    q = "用户列表并可视化分析"
    assert looks_like_compound_query_with_viz(q) is True
    assert looks_like_pure_result_followup(q) is False


def test_pure_viz_followup_without_prior_query_verbs():
    q = "可视化分析一下"
    assert looks_like_compound_query_with_viz(q) is False
    assert looks_like_pure_result_followup(q) is True


def test_chart_display_followup_is_not_compound_new_query():
    q = "柱状图显示吧"
    assert looks_like_compound_query_with_viz(q) is False
    assert looks_like_pure_result_followup(q) is True
