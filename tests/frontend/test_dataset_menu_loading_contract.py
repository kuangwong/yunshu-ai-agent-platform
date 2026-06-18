from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_dataset_menu_loading_contract(source: str) -> None:
    assert "const datasetMenuLoading = ref(false);" in source
    assert "if (datasetMenuLoading.value || isProcessing.value) {" in source
    assert "datasetMenuLoading.value = true;" in source
    assert "isProcessing.value = true;" in source
    assert "datasetMenuLoading.value = false;" in source
    assert "isProcessing.value = false;" in source
    assert "case \"/dataset_menu\":" in source
    assert "let datasetMenuThoughtTimer: ReturnType<typeof setInterval> | null = null;" in source
    assert "datasetMenuThoughtTimer = setInterval(() => {" in source
    assert "navMsg.value.thoughtDuration = (" in source
    assert "clearInterval(datasetMenuThoughtTimer);" in source
    assert "datasetMenuThoughtTimer = null;" in source
    assert "DatasetCapabilityMenu" in source
    assert "datasetNavigation?: DatasetNavigationPayload;" in source
    assert "navMsg.value.datasetNavigation = payload;" in source
    assert "lockToDataQueryAgentForDatasetMenu" in source
    assert "capabilities.includes(\"data_query\")" in source
    assert "refreshDatasetMenuNavigation" in source
    assert "refresh: true" in source
    assert "recordDatasetMenuQuestionClick" in source
    assert "/api/v1/chat/dataset-menu/click" in source
    assert "dataset_menu_hash" in source
    assert "📚 数据门户" in source
    assert "系统 · 数据门户" in source
    assert "正在生成我的数据门户，请稍后" in source


def test_embed_chat_locks_input_while_dataset_menu_loads():
    _assert_dataset_menu_loading_contract(_source("frontend/src/views/EmbedChat.vue"))


def test_agent_debug_locks_input_while_dataset_menu_loads():
    _assert_dataset_menu_loading_contract(_source("frontend/src/views/AgentDebug.vue"))


def test_dataset_capability_menu_component_contract():
    source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    assert "defineEmits" in source
    assert "quick-question" in source
    assert "record-question-click" in source
    assert "refresh" in source
    assert "payload.generated_at" in source
    assert "payload.dataset_menu_hash" in source
    assert "我的数据门户" in source
    assert "click_count" in source
    assert "handleQuestionClick" in source
    assert "props.payload.groups" in source
    assert "group.questions" in source
    assert "group.followups" in source
    assert "related_data" in source
    assert "GROUP_REFRESH_COOLDOWN_MS" in source
    assert "startGroupRefreshCooldown" in source
    assert "换一批太频繁，请稍后再试" in source
    assert "QUESTIONS_SECTION_TIP" in source
    assert "FOLLOWUPS_SECTION_TIP" in source
    assert "该场景的入门示例问题" in source
    assert "延伸探索型追问" in source


def test_thought_step_dimming_contract():
    source = _source("frontend/src/utils/turnLogDisplay.ts")
    assert "isActiveThoughtStep" in source
    assert "isDimmedThoughtStep" in source
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "isDimmedThoughtStep(log, msg.isThinking)" in embed
    assert "进行中" in embed


def test_thought_step_timer_contract():
    handlers = _source("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "finalizePendingStreamLogs" in handlers
    assert "isLiveThoughtStepTimer" in handlers
    assert "findPendingAgentReplyLog" in handlers
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "isLiveThoughtStepTimer(log, allLogs || [])" in embed
    assert "finalizeAllPendingStreamLogs(agentMsg.value)" in embed


def test_dataset_portal_drawer_pin_contract():
    source = _source("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    assert 'defineModel<boolean>("pinned"' in source
    assert "v-if=\"!pinned\"" in source
    assert "pointer-events-none" in source
    assert "钉住侧栏（去掉遮罩，可继续浏览聊天）" in source
    assert "已钉住" in source
