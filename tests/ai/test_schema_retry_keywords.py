import pytest
from unittest.mock import MagicMock

from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

pytestmark = pytest.mark.no_infrastructure


def test_clean_schema_retry_phrase_filters_emoji():
    # 测试 Emoji 清理
    raw_text = "机房 🙋"
    cleaned = DataAgentRunner._clean_schema_retry_phrase(raw_text)
    assert cleaned == "机房"


def test_clean_schema_retry_phrase_filters_ui_stopwords():
    # 测试 UI 停用词清理
    raw_text = "为您找到以下数据 机房的 信息 详细"
    cleaned = DataAgentRunner._clean_schema_retry_phrase(raw_text)
    # "为您", "以下", "数据", "信息", "详细" 都会被过滤掉，只剩下 "机房 的" ("的"因为不在 stopword，但因为只是一个单字，后置过滤)
    assert "为您" not in cleaned
    assert "数据" not in cleaned
    assert "信息" not in cleaned
    assert "机房" in cleaned


def test_prepare_controlled_schema_retry_keywords_prioritizes_extracted():
    # 创建 Runner 实例
    runner = DataAgentRunner(config=MagicMock())
    runner._schema_search_keywords = "机房 列表"
    runner._standalone_query = "为您 到以下数据 机房的 信息 🙋"  # 被污染的 query

    state = _DataRunState()
    state.last_schema_keywords = "机房"

    # 调用重试词预备，被污染的 user_question 被传入
    user_question = "为您 到以下数据 机房的 信息 🙋 机房详情"
    runner._prepare_controlled_schema_retry_keywords(state, user_question)

    keywords = state.controlled_schema_retry_keywords
    # 既然有 last_schema_keywords 和 _schema_search_keywords，那么 sources 就应该只有这俩
    # 生成的关键词中应当包含 "机房" 和 "列表" (以及它们的笛卡尔积组合，如 "机房列表")
    assert "机房" in keywords
    # 但绝不能含有任何 UI 废话和 Emoji 废话
    assert "为您" not in keywords
    assert "🙋" not in keywords
    assert "数据" not in keywords
    assert "信息" not in keywords


def test_prepare_controlled_schema_retry_keywords_fallback_with_cleanup():
    # 测试没有任何已提炼核心词的兜底情况
    runner = DataAgentRunner(config=MagicMock())
    runner._schema_search_keywords = ""
    runner._standalone_query = ""

    state = _DataRunState()
    state.last_schema_keywords = ""

    # 虽然由于没有任何已提炼的词导致用 user_question 兜底，但由于进行了严格的清理：
    user_question = "为您 到以下数据 机房的 信息 🙋"
    runner._prepare_controlled_schema_retry_keywords(state, user_question)

    keywords = state.controlled_schema_retry_keywords
    # 因为 "为您"、"到以下"、"数据"、"信息"、"🙋" 被正则和 stopword 抹除，剩下的只有 "机房"
    assert "机房" in keywords
    assert "为您" not in keywords
    assert "数据" not in keywords
    assert "🙋" not in keywords
