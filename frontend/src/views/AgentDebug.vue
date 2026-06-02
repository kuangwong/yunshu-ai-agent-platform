<script setup lang="ts">
// ... imports ...
import { ref, nextTick, watch, onUnmounted, reactive, onMounted, computed } from "vue";
import { useRoute } from "vue-router";
import TraceLogViewer from "@/components/TraceLogViewer.vue";
import DebugConfigPanel from "@/components/DebugConfigPanel.vue";
import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";
import MessageRenderer from "@/components/MessageRenderer.vue";
import MentionList from "@/components/agent/MentionList.vue"; // New Import
import axios from "@/utils/axios";
import { finalizeConversation } from "@/utils/conversationFinalize";
import { createSseLineParser } from "@/utils/chartRenderer";
import { useToast } from "../composables/useToast";

const route = useRoute();
const { showToast } = useToast();

// ... existing code ...

// Mention State
const showMentionList = ref(false);
const mentionKeyword = ref("");
const mentionPosition = reactive({ top: 0, left: 0 });
const mentionListRef = ref<any>(null);

const handleInput = (e: Event) => {
  const target = e.target as HTMLTextAreaElement;
  const val = target.value;
  const cursor = target.selectionStart;
  
  // Check for @
  const lastAt = val.lastIndexOf('@', cursor - 1);
  if (lastAt !== -1) {
    const query = val.slice(lastAt + 1, cursor);
    if (!query.includes(' ')) {
        // Fetch allowed agents if not already fetched
        // Optimization: We can fetch this once or lazy load. 
        // For Debug view, we might want to use the same list as the dropdown (all visible),
        // OR strict allowed list. Let's use /allowed for consistency with EmbedChat.
        if (agents.value.length === 0) { // Fallback if main list empty
             fetchAgents(); 
        }
        
        // Use 'agents' list for now, but ideally should be a separate 'allowedAgents' list
        // filtering locally is fine for Debug view as it shows all by default.
        mentionKeyword.value = query;
        showMentionList.value = true;
        
        const rect = target.getBoundingClientRect();
        mentionPosition.top = rect.top - 220; 
        mentionPosition.left = rect.left + 20;
        return;
    }
  }
  showMentionList.value = false;
};

const handleMentionSelect = (agent: any) => {
  const target = inputTextarea.value;
  if (!target) return;
  
  const val = userInput.value;
  const cursor = target.selectionStart;
  const lastAt = val.lastIndexOf('@', cursor - 1);
  
  if (lastAt !== -1) {
      const before = val.slice(0, lastAt);
      const after = val.slice(cursor);
      // Insert Agent Display Name
      const insertText = `@${agent.display_name} `;
      userInput.value = before + insertText + after;
      
      // Move cursor
      nextTick(() => {
          target.selectionStart = target.selectionEnd = before.length + insertText.length;
          target.focus();
      });
      
      // Auto-switch Agent Context
      debugMode.value = 'specific';
      agentParams.agent_id = agent.id;
  }
  showMentionList.value = false;
};

// ... existing handleKeydown ...
const handleKeydown = (e: KeyboardEvent) => {
  // Prevent Enter key from sending message while IME composition is active
  if (e.isComposing) return;

  // Delegate to MentionList if visible
  if (showMentionList.value && mentionListRef.value) {
      if (mentionListRef.value.handleKeydown(e)) {
          return;
      }
  }

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
};

// --- New Features State ---
const showSessionPreview = ref(false);
const conversationTurns = ref<any[]>([]);
const loadingTrace = ref(false);
const previewConversationId = ref<string>("");

const aggregatedHistoryList = computed(() => {
  if (!historyList.value.length) return [];
  const groups: Record<string, any[]> = {};
  const orderedKeys: string[] = [];
  
  // 1. Group by conversation_id
  historyList.value.forEach(item => {
    const cid = item.conversation_id || `trace_${item.trace_id}`;
    if (!groups[cid]) {
      groups[cid] = [];
      orderedKeys.push(cid);
    }
    groups[cid].push(item);
  });

    // 2. Map to representative item with turn_count
  return orderedKeys.map(key => {
    const items = groups[key];
    if (!items || items.length === 0) return null;
    // Use the latest item (or first, depending on sort) as representative
    // Assuming historyList is sorted by created_at desc? 
    // Usually fetching history returns latest first.
    const representative = items[0]; 
    
    // If we have multiple items locally, that's the count (frontend aggregation).
    // If we have 1 item, it might be a pre-grouped item from backend with a count.
    const count = items.length > 1 ? items.length : (representative.turn_count || items.length);

    return {
      ...representative,
      turn_count: count
    };
  }).filter(Boolean);
});

const formatDate = (dateStr: string) => { 
  if (!dateStr) return "-"; 
  try {
    return new Date(dateStr).toLocaleString(); 
  } catch (e) { return dateStr; }
};

const openSessionPreview = async (traceIdOrItem: string | any) => {
  const traceId = typeof traceIdOrItem === 'string' ? traceIdOrItem : traceIdOrItem?.trace_id;
  const convId = typeof traceIdOrItem === 'string' ? null : traceIdOrItem?.conversation_id;
  
  if (!traceId && !convId) return;
  
  showSessionPreview.value = true;
  loadingTrace.value = true;
  conversationTurns.value = [];
  previewConversationId.value = "";
  
  try {
     if (convId) {
         previewConversationId.value = convId;
         await fetchSessionTurns(convId);
     } else if (traceId) {
        const res = await axios.get(`/api/v1/chat/logs/${traceId}`);
        if (res.data?.data) {
            const cid = res.data.data.history?.conversation_id;
            if (cid) {
                previewConversationId.value = cid;
                await fetchSessionTurns(cid);
            } else if (res.data.data.history) {
                // Single trace fallback
                conversationTurns.value = [{ 
                    ...res.data.data.history, 
                    steps: res.data.data.steps || [], 
                    isExpanded: true,
                    trace_id: traceId
                }];
            }
        }
     }
  } catch (e) {
    console.error("Failed to load session preview", e);
  } finally {
    loadingTrace.value = false;
  }
};

const fetchSessionTurns = async (cid: string) => {
    try {
        const historyRes = await axios.get(`/api/v1/chat/history`, {
            params: { conversation_id: cid, page_size: 100 }
        });
        if (historyRes.data?.data?.items) {
            // Sort by created_at (oldest first for chat view)
            const sorted = historyRes.data.data.items.reverse(); 
            
            // 全部默认折叠
            conversationTurns.value = sorted.map((item: any) => ({
                ...item,
                steps: [], // Lazy load steps
                loading: false,
                isExpanded: false
            }));
        }
    } catch (e) { console.error(e); }
};

const toggleTurnSteps = async (turn: any) => {
    turn.isExpanded = !turn.isExpanded;
    if (turn.isExpanded && (!turn.steps || turn.steps.length === 0)) {
        turn.loading = true;
        try {
            const res = await axios.get(`/api/v1/chat/logs/${turn.trace_id}`);
            if (res.data?.data?.steps) turn.steps = res.data.data.steps;
        } catch (e) {
            console.error("Failed to fetch steps for turn", e);
        } finally {
            turn.loading = false;
        }
    }
};

const continueChatFromTrace = () => {
    // 1. Prefer explicit conversation ID
    let targetId = previewConversationId.value;
    
    // 2. Fallback to extracting from turns
    if (!targetId && conversationTurns.value.length > 0) {
        const targetTurn = conversationTurns.value[conversationTurns.value.length - 1];
        if (targetTurn?.conversation_id) {
            targetId = targetTurn.conversation_id;
        }
    }

    if (targetId) {
        const previousId = conversationId.value;
        if (previousId && previousId !== targetId) {
            finalizeConversationInBackground(previousId);
        }
        conversationId.value = targetId;
        localStorage.setItem("agent_debug_conv_id", targetId);
        messages.value = [];
        loadSessionHistory(targetId);
        showSessionPreview.value = false;
    } else {
        alert("无法继续：该会话可能未正确保存或仅为单次追踪记录。");
    }
};


import { modelApi, type AIModel } from "../api/model";

import ConfirmModal from "@/components/ConfirmModal.vue";

interface SlashCommand {
  id?: number;
  label: string;
  command: string;
  sort_order: number;
}

const agentParams = reactive<{
  agent_id?: string | null; // null = Auto Router
  version_id?: string | null;
}>({
  agent_id: null,
  version_id: null,
});

// History State
const historyList = ref<any[]>([]);
const loadingHistory = ref(false);
const historyKeyword = ref("");
let searchTimer: any = null;

const fetchHistory = async () => {
  loadingHistory.value = true;
  try {
    const params: any = { page: 1, page_size: 50, group_by_conversation: true };
    if (agentParams.agent_id) {
      params.agent_id = agentParams.agent_id;
    }
    if (historyKeyword.value) {
      params.keyword = historyKeyword.value;
    }
    const res = await axios.get("/api/v1/chat/history", { params });
    if (res.data?.data) historyList.value = res.data.data.items || [];
  } catch (e) {
    console.error("Failed to fetch history", e);
  } finally {
    loadingHistory.value = false;
  }
};

watch(historyKeyword, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    fetchHistory();
  }, 500);
});

watch(
  () => agentParams.agent_id,
  () => {
    fetchHistory();
  }
);

// Agents State for Dropdown
const agents = ref<any[]>([]);
const debugMode = ref<"auto" | "specific">("auto");
const showShortcuts = ref(true);
const slashCommands = ref<any[]>([]); // Dynamic commands

const fetchAgents = async () => {
  try {
    const res = await axios.get("/api/portal/agents/");
    if (res.data) {
      agents.value = res.data.filter((a: any) => a.is_enabled !== false);
    }
  } catch (e) {
    console.error("Failed to fetch agents", e);
  }
};

const hideDebugLikeDislikeForHostedAgent = computed(() => {
  if (!agentParams.agent_id) return false;
  const ag = agents.value.find((a) => a.id === agentParams.agent_id);
  const t = ag?.engine_type;
  return t === "RAGFLOW" || t === "OPENCLAW";
});

const fetchSlashCommands = async () => {
  try {
    const res = await axios.get("/api/portal/slash-commands/");
    if (res.data) slashCommands.value = res.data;
  } catch (e) {
    console.error("Failed to fetch slash commands", e);
  }
};

const availableModels = ref<AIModel[]>([]);
const fetchModels = async () => {
  try {
    const res = await modelApi.list();
    availableModels.value = res.data.filter(
      (m) => m.type === "llm" && m.is_active
    );
  } catch (e) {
    console.error("Failed to fetch models", e);
  }
};

const messages = ref<Message[]>([]);
const displayMessages = computed(() => {
  const raw = messages.value;
  if (!raw || raw.length === 0) return [];
  
  const filtered: Message[] = [];
  if (raw[0]) filtered.push(raw[0]);
  
  for (let i = 1; i < raw.length; i++) {
    const prev = raw[i - 1];
    const curr = raw[i];
    // Filter out consecutive duplicates with same role and content
    // EXCEPT if the current message has citations or logs (which makes it unique/active)
    const hasMetadata = (curr?.citations && curr.citations.length > 0) || (curr?.logs && curr.logs.length > 0);
    if (prev && curr && curr.role === prev.role && curr.content === prev.content && !curr.isThinking && !hasMetadata) {
      continue;
    }
    if (curr) filtered.push(curr);
  }
  return filtered;
});
const conversationId = ref("");

const debugAuthHeaders = (): Record<string, string> | undefined => {
  const key = localStorage.getItem("api_key");
  return key ? { "X-API-Key": key } : undefined;
};

const finalizeConversationInBackground = (cid: string) => {
  void finalizeConversation(cid, debugAuthHeaders());
};

const generateNewConversation = (isManual = false) => {
  const previousId = conversationId.value;
  if (previousId) {
    finalizeConversationInBackground(previousId);
  }
  conversationId.value = crypto.randomUUID();
  localStorage.setItem("agent_debug_conv_id", conversationId.value);
  if (isManual) {
    messages.value = [];
    loadGreeting();
  }
};

const loadSessionHistory = async (id: string) => {
  try {
    const res = await axios.get(`/api/v1/chat/conversation/${id}`, {
      headers: { 'X-API-Key': localStorage.getItem('api_key') }
    });
    if (res.data?.data && Array.isArray(res.data.data.messages)) {
      // Deduplicate: Filter out consecutive messages with same role and content
      const rawMessages = res.data.data.messages;
      const validMessages: any[] = [];
      
      if (rawMessages.length > 0) {
        validMessages.push(rawMessages[0]);
        for (let i = 1; i < rawMessages.length; i++) {
          const prev = rawMessages[i-1];
          const curr = rawMessages[i];
          // Simple deduplication check
          if (curr.role === prev.role && curr.content === prev.content) {
            continue; // Skip duplicate
          }
          validMessages.push(curr);
        }
      }

      const historyMsg: Message[] = validMessages.map(
        (m: any, idx: number) => ({
          id: Date.now() + idx, 
          trace_id: m.trace_id,
          role: m.role === "assistant" ? "agent" : m.role,
          content: m.content as string,
          logs: [], 
          isThinking: false,
          isHistory: true, // Mark as history
          feedback: m.feedback,
          agentName: m.agent_name || undefined,
        })
      );
      if (historyMsg.length > 0) {
        // Add Separator with Timestamp
        const lastMsg = res.data.data.messages[res.data.data.messages.length - 1];
        let timeStr = "";
        if (lastMsg && lastMsg.timestamp) {
             try {
                const date = new Date(lastMsg.timestamp);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');
                timeStr = `${year}-${month}-${day} ${hours}:${minutes}`;
            } catch (e) { }
        }

        historyMsg.push({
          id: Date.now() + historyMsg.length,
          role: "system",
          content: "以上是历史会话，可以重置会话清除",
          timestamp: timeStr 
        });

        messages.value = historyMsg;
        nextTick(scrollToBottom);
        return; 
      }
    }
    // If empty or fail, load greeting
    loadGreeting();
  } catch (e) {
    console.warn("Failed to load session history", e);
    loadGreeting();
  }
};

const currentUser = ref<any>(null);

const fetchCurrentUser = async () => {
  try {
    const res = await axios.get("/api/portal/auth/me");
    if (res.data?.data) {
      currentUser.value = res.data.data;
    }
  } catch (e) {
    console.error("Failed to fetch user info", e);
  }
};

onMounted(() => {
  fetchCurrentUser();
  // Check for traceId in URL
  const queryTraceId = route.query.traceId as string;
  if (queryTraceId) {
    openFullLogs(queryTraceId);
  }

  // Check for Agent Debug Context (agent_id, version_id)
  const qAgentId = route.query.agent_id as string;
  const qVersionId = route.query.version_id as string;

  fetchAgents();
  fetchSlashCommands(); // Fetch dynamic commands
  fetchModels();
  fetchModels();
  fetchHistory(); // Auto-load history on mount

  if (qAgentId) {
    agentParams.agent_id = qAgentId;
    debugMode.value = "specific";
  } else {
    // Default to Auto Router mode (agent_id = null)
    agentParams.agent_id = null;
    debugMode.value = "auto";
  }

  // Initialize or Retrieve Conversation ID
  const savedId = localStorage.getItem("agent_debug_conv_id");
  if (savedId) {
    conversationId.value = savedId;
    loadSessionHistory(savedId);
  } else {
    // If no session, generate key
    generateNewConversation();
  }

  if (qVersionId) agentParams.version_id = qVersionId;

  if (qVersionId) {
    // Add a system update message to inform user they are in preview mode
    messages.value.push({
      id: Date.now(),
      role: "agent",
      content: `<div class="bg-amber-50 border-l-4 border-amber-400 p-4 rounded-r-lg mb-4">
        <div class="flex">
          <div class="flex-shrink-0">
            <svg class="h-5 w-5 text-amber-400" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
            </svg>
          </div>
          <div class="ml-3">
            <h3 class="text-sm leading-5 font-medium text-amber-800">
              调试预览模式 (Debug Preview Mode)
            </h3>
            <div class="mt-2 text-sm leading-5 text-amber-700">
              <p>当前会话已绑定到特定版本，修改将实时生效。</p>
              <ul class="list-disc pl-5 space-y-1 mt-1">
                <li><strong>Agent ID:</strong> <code class="bg-amber-100 px-1 py-0.5 rounded text-xs font-mono">${
                  qAgentId || "default"
                }</code></li>
                <li><strong>Version ID:</strong> <code class="bg-amber-100 px-1 py-0.5 rounded text-xs font-mono">${qVersionId}</code></li>
              </ul>
            </div>
          </div>
        </div>
      </div>`,
    });
  }
});

const copyContent = async (content: string, event: Event) => {
  try {
    await navigator.clipboard.writeText(content);

    // Visual feedback
    const btn = event.currentTarget as HTMLElement;
    const originalHtml = btn.innerHTML;

    btn.innerHTML = `<svg class="w-3 h-3 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg><span>已复制</span>`;
    btn.classList.add("text-green-600", "bg-green-50");
    btn.classList.remove("text-gray-400", "hover:text-gray-600");

    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.classList.remove("text-green-600", "bg-green-50");
      btn.classList.add("text-gray-400", "hover:text-gray-600");
    }, 2000);
  } catch (err) {
    console.error("Failed to copy:", err);
  }
};

const exportData = async (traceId: string, format = 'xlsx') => {
  if (!traceId) return;
  try {
    const response = await axios.get(`/api/v1/chat/export/data/${traceId}`, {
      params: { format },
      responseType: 'blob'
    });
    
    const blob = new Blob([response.data], { 
      type: format === 'xlsx' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'text/csv' 
    });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    link.setAttribute('download', `debug_export_${dateStr}_${traceId.slice(0, 8)}.${format}`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    showToast("数据导出成功", "success");
  } catch (e) {
    console.error("Export failed", e);
    showToast("导出失败：未找到可导出数据", "error");
  }
};

// ... existing code ...

interface LogEntry {
  id: string | number;
  title: string;
  details: string;
  status: "pending" | "success" | "error" | "warning";
  isExpanded: boolean;
  isDebug?: boolean;
  isRouter?: boolean;
  category?: 'router' | 'sql' | 'knowledge' | 'tool' | 'intent' | 'permission' | 'default';
  model?: string;
  temperature?: number;
}

// ... inside script ...

interface Message {
  id: number;
  role: "user" | "agent" | "system";
  content: string;
  rawContent?: string; // Store original markdown for copying
  logs?: LogEntry[];
  isThinking?: boolean;
  timestamp?: string;
  intent?: string;
  rawPrompt?: any; // Store raw prompt data
  trace_id?: string; // Associated Trace ID for full logs
  citations?: any[]; // Knowledge base references
  isCitationsExpanded?: boolean; // Collapsible toggle
  agentName?: string; // Which agent responded (ID or Name)
  agentDisplayName?: string; // Human readable display name
  isThoughtExpanded?: boolean; // Toggle for the logs block
  thoughtStartTime?: number; // Timestamp when thinking started
  thoughtDuration?: string; // Duration in seconds (formatted)
  thinkingText?: string; // Dynamic thinking text
  isGreeting?: boolean; // Is this the initial greeting message?
  isHistory?: boolean; // Is this a history separator or message?
  feedback?: "up" | "down" | null;
}

// --- Debug Config State ---
const showHistorySidebar = ref(false);
const showConfigPanel = ref(true);
const showLogicFlowModal = ref(false);
const isConfigPanelFloating = ref(false);
const debugConfig = reactive({
  model: "", // Empty means default
  temperature: 0.0,
  dryRun: false, // SQL Review Mode
  returnRawPrompt: true, // Always verify context
  enableMultiAgent: true, // Multi-agent collaboration
  systemPromptOverride: "", // Prompt Engineering
  injectedContext: [] as { key: string; value: string }[], // Manual Context Injection
});



const loadingConfig = ref(false);
const loadCurrentPrompt = async () => {
  if (!agentParams.agent_id) {
    alert("请先选择一个特定的智能体");
    return;
  }

  loadingConfig.value = true;
  try {
    const res = await axios.get(
      `/api/portal/agents/${agentParams.agent_id}/active-config`
    );
    if (res.data && res.data.system_prompt) {
      debugConfig.systemPromptOverride = res.data.system_prompt;
      // Optional: also load model and temperature
      if (res.data.model_name) debugConfig.model = res.data.model_name;
      if (res.data.temperature !== undefined)
        debugConfig.temperature = res.data.temperature;
    }
  } catch (e) {
    console.error("Failed to load agent config", e);
    alert("加载配置失败，请检查该智能体是否已发布版本");
  } finally {
    loadingConfig.value = false;
  }
};

const showRawPromptModal = ref(false);
const showFullLogViewer = ref(false);
const selectedRawPrompt = ref<any>(null);
const activeTraceId = ref("");

// --- Agent Context State ---
const agentContext = ref<Record<string, any>>({});

const clearContext = (key?: string) => {
  if (key) {
    delete agentContext.value[key];
  } else {
    agentContext.value = {};
  }
};

const loadGreeting = async () => {
  try {
    // Show a temporary placeholder while loading
    messages.value = [
      {
        id: Date.now(),
        role: "agent",
        content: "", // Show empty content with thinking indicator instead of text
        isThinking: true,
      },
    ];

    const res = await axios.get("/api/v1/chat/greeting");
    if (res.data?.data && res.data.data.greeting) {
      messages.value = [
        {
          id: Date.now(),
          role: "agent",
          content: res.data.data.greeting,
          isGreeting: true,
        },
      ];
    }
  } catch (e) {
    messages.value = [
      {
        id: Date.now(),
        role: "agent",
        content: "您好！我是云枢智能体，期待为您服务。",
        isGreeting: true,
      },
    ];
  }
};

const clearHistory = () => {
  generateNewConversation(true);
  agentContext.value = {};
  activeTraceId.value = "";
  showFullLogViewer.value = false;
};

const openFullLogs = (traceId: string) => {
  activeTraceId.value = traceId;
  showFullLogViewer.value = true;
};

const openRawPrompt = (msg: Message) => {
  if (msg.rawPrompt) {
    selectedRawPrompt.value = msg.rawPrompt;
    showRawPromptModal.value = true;
  }
};

const showCommandManager = ref(false);
const editingCommand = ref<SlashCommand>({
  label: "",
  command: "",
  sort_order: 0,
});
const isEditingCmd = ref(false);
const showDeleteConfirm = ref(false);
const commandToDelete = ref<number | null>(null);

const openCommandManager = () => {
  showCommandManager.value = true;
  resetCommandForm();
};

const resetCommandForm = () => {
  editingCommand.value = { label: "", command: "", sort_order: 0 };
  isEditingCmd.value = false;
};

const editCommand = (cmd: any) => {
  isEditingCmd.value = true;
  editingCommand.value = { ...cmd };
};

const confirmDeleteCommand = (cmdId: number) => {
  commandToDelete.value = cmdId;
  showDeleteConfirm.value = true;
};

const performDeleteCommand = async () => {
  if (commandToDelete.value === null) return;
  try {
    await axios.delete(`/api/portal/slash-commands/${commandToDelete.value}`);
    await fetchSlashCommands();
    showDeleteConfirm.value = false;
    commandToDelete.value = null;
  } catch (e) {
    console.error("Failed to delete command", e);
    alert("删除失败");
  }
};

const saveCommand = async () => {
  if (!editingCommand.value.label || !editingCommand.value.command) {
    alert("请填写完整信息");
    return;
  }

  try {
    if (isEditingCmd.value && editingCommand.value.id) {
      // Update
      await axios.put(
        `/api/portal/slash-commands/${editingCommand.value.id}`,
        editingCommand.value
      );
    } else {
      // Create
      await axios.post("/api/portal/slash-commands/", editingCommand.value);
    }
    await fetchSlashCommands();
    resetCommandForm();
  } catch (e) {
    console.error("Failed to save command", e);
    alert("保存失败");
  }
};

const closeModals = () => {
  // Do not close config panel
  showCommandManager.value = false;
  showLogicFlowModal.value = false;
  showRawPromptModal.value = false;
  selectedRawPrompt.value = null;
};

const handleImageUpload = () => {
  alert("多模态图片上传功能开发中...");
};

// --- Export Chat ---
const exportChat = () => {
  if (messages.value.length === 0) {
    alert("暂无对话记录");
    return;
  }
  
  const thread = messages.value.map(m => {
    return `### ${m.role === 'user' ? 'User' : m.agentName || 'Agent'}\n\n${m.content}\n`;
  }).join("\n---\n\n");
  
  const blob = new Blob([thread], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `chat_export_${new Date().toISOString().slice(0,19).replace('T','_').replace(/:/g,'-')}.md`;
  a.click();
  URL.revokeObjectURL(url);
};

// --- Drag & Drop Sorting ---
const draggedItemIndex = ref<number | null>(null);

const onDragStart = (event: DragEvent, index: number) => {
  draggedItemIndex.value = index;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.dropEffect = "move";
  }
};

const onDrop = async (_event: DragEvent, targetIndex: number) => {
  if (draggedItemIndex.value === null || draggedItemIndex.value === targetIndex)
    return;

  // Reorder local array
  const item = slashCommands.value.splice(draggedItemIndex.value, 1)[0];
  slashCommands.value.splice(targetIndex, 0, item);

  draggedItemIndex.value = null;

  // Update sort_order and save
  await updateSortOrders();
};

const updateSortOrders = async () => {
  const updates = slashCommands.value.map((cmd, index) => ({
    id: cmd.id,
    label: cmd.label,
    command: cmd.command,
    sort_order: (index + 1) * 10,
  }));

  // Optimistic update
  slashCommands.value.forEach((cmd, index) => {
    cmd.sort_order = (index + 1) * 10;
  });

  try {
    // Parallel update requests
    await Promise.all(
      updates.map((u) => axios.put(`/api/portal/slash-commands/${u.id}`, u))
    );
  } catch (e) {
    console.error("Failed to update sort order", e);
    // Revert logic could be added here if strict consistency is needed
  }
};

// --- Edit & Resend ---
const editingMsgId = ref<number | null>(null);
const editContent = ref("");

const startEdit = (msg: Message) => {
  editingMsgId.value = msg.id;
  editContent.value = msg.content;
};

const cancelEdit = () => {
  editingMsgId.value = null;
  editContent.value = "";
};

const saveAndResend = async () => {
  if (editingMsgId.value === null) return;
  const msgIndex = messages.value.findIndex(m => m.id === editingMsgId.value);
  if (msgIndex === -1) return;
  
  // Update content
  const newContent = editContent.value.trim();
  if (!newContent) return; // Don't allow empty
  
  // Truncate history: keep up to this message, and remove everything after
  messages.value = messages.value.slice(0, msgIndex);
  
  // Reset ID and state
  editingMsgId.value = null;
  editContent.value = "";
  
  // Set as new input and send
  userInput.value = newContent;
  await sendMessage();
};

const regenerate = async (agentMsg: Message) => {
  if (isProcessing.value) return;
  
  // Find index of this agent message
  const idx = messages.value.findIndex(m => m.id === agentMsg.id);
  if (idx === -1) return;
  
  // Verify previous message is user
  const userMsg = messages.value[idx - 1];
  if (!userMsg || userMsg.role !== 'user') return;
  
  // Remove this agent message
  messages.value.splice(idx, 1);
  
  // Set user input to previous query and resend
  // Need to avoid duplicate user message being added in sendMessage, so we manually trigger backend call or adjust sendMessage.
  // Actually, easiest way: remove agent message, set userInput = userMsg.content, REMOVE userMsg as well (sendMessage adds it back)
  // OR: Modify sendMessage to accept content and skip adding user msg if needed?
  // Let's go with: Remove BOTH agent and user message, then sendMessage(userMsg.content)
  
  messages.value.splice(idx - 1, 1); // Remove user message
  userInput.value = userMsg.content;
  await sendMessage();
};

const userInput = ref("");
const isProcessing = ref(false);
const inputTextarea = ref<HTMLTextAreaElement | null>(null);
const messagesContainer = ref<HTMLDivElement | null>(null);

let abortController: AbortController | null = null;
let thoughtTimer: any = null;

// Auto-scroll
const isUserAtBottom = ref(true);

const handleScroll = () => {
  if (!messagesContainer.value) return;
  const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value;
  // Threshold 50px
  isUserAtBottom.value = scrollHeight - scrollTop - clientHeight < 50;
};

const scrollToBottom = (force = false) => {
  if (messagesContainer.value && (isUserAtBottom.value || force)) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
  }
};

watch(
  [
    () => messages.value.length,
    () => messages.value[messages.value.length - 1]?.content?.length,
    () => messages.value[messages.value.length - 1]?.logs?.length,
  ],
  () => {
    nextTick(scrollToBottom);
  }
);

const handleQuickQuestion = (question: string) => {
  if (!question) return;
  userInput.value = question;
  sendMessage();
};

const handleShowCitation = (msg: Message, citeId: string) => {
  console.log("[Citation Debug] UI Action - Target ID:", citeId);
  if (!msg.citations || msg.citations.length === 0) return;
  
  // 1. Match by ID
  let target = msg.citations.find(c => 
    String(c.chunk_id) === String(citeId) || 
    String(c.id) === String(citeId) ||
    String(c.chunk_id)?.endsWith(String(citeId))
  );
  
  // 2. Match by index
  if (!target && /^\d+$/.test(citeId)) {
      const idx = parseInt(citeId);
      target = msg.citations[idx - 1] || msg.citations[idx];
  }

  if (target) {
    msg.isCitationsExpanded = true;
    msg.citations.forEach(c => c.isExpanded = false);
    target.isExpanded = true;
    console.log("[Citation Debug] Opened:", target.doc_name);
  }
};


const handleFeedback = async (msg: Message, type: "up" | "down") => {
  const oldFeedback = msg.feedback;
  if (msg.feedback === type) {
    msg.feedback = null;
  } else {
    msg.feedback = type;
  }
  
  // 立即给予 UI 反馈提示 (乐观更新)
  if (msg.feedback) {
    showToast(msg.feedback === 'up' ? "感谢您的点赞！" : "已记录您的反馈，我们将持续改进。", "success");
  } else {
    showToast("已取消反馈", "info");
  }

  if (!msg.trace_id) {
    console.warn("Cannot post feedback to server: missing trace_id");
    return;
  }

  try {
    await axios.post("/api/portal/chat/feedback", {
      trace_id: msg.trace_id,
      feedback: msg.feedback || "none",
      user_id: currentUser.value?.user_id || "anonymous"
    });
  } catch (error) {
    console.error("Failed to post feedback", error);
    msg.feedback = oldFeedback;
  }
};


const stopGeneration = () => {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  isProcessing.value = false;
  
  // Stop thinking timer if active
  if (thoughtTimer) {
    clearInterval(thoughtTimer);
    thoughtTimer = null;
  }

  // Update last message status if needed
  const lastMsg = messages.value[messages.value.length - 1];
  if (lastMsg && lastMsg.role === 'agent' && lastMsg.isThinking) {
    lastMsg.isThinking = false;
    lastMsg.content += "\n[用户终止生成]";
  }
};

const sendMessage = async () => {
  const content = userInput.value.trim();
  if (!content || isProcessing.value) return;

  // 1. Add User Message
  messages.value.push({
    id: Date.now(),
    role: "user",
    content: content,
  });
  
  // Force scroll for user message
  nextTick(() => scrollToBottom(true));

  userInput.value = "";
  isProcessing.value = true;

  // 2. Add Agent Placeholder
  const agentMsgId = Date.now() + 1;
  const agentMsg = ref<Message>({
    id: agentMsgId,
    role: "agent",
    content: "",
    isThinking: true,
    logs: [],
    citations: [],
    isCitationsExpanded: false,
    isThoughtExpanded: true, // Default expanded to show progress
    thoughtStartTime: Date.now(),
    thoughtDuration: "0.0",
    thinkingText: "云枢正在处理您的请求...",
  });
  messages.value.push(agentMsg.value);

  // Thinking Messages
  const THINKING_MESSAGES = [
    "正在分析任务...",
    "正在组织语言...",
  ];

  // Start Timer
  if (thoughtTimer) clearInterval(thoughtTimer);
  let ticks = 0;
  thoughtTimer = setInterval(() => {
    ticks++;
    if (agentMsg.value.thoughtStartTime) {
      const duration = (Date.now() - agentMsg.value.thoughtStartTime) / 1000;
      agentMsg.value.thoughtDuration = duration.toFixed(1);
    }
    
    // Rotate message every 3 seconds (30 * 100ms)
    if (ticks % 30 === 0) {
      const msgIndex = (ticks / 30) % THINKING_MESSAGES.length;
      agentMsg.value.thinkingText = THINKING_MESSAGES[msgIndex];
    }
  }, 100);

  // 3. Call Real API with SSE
  abortController = new AbortController();

  try {
    // Prepare Debug Options
    const debugOptions: any = {
      return_raw_prompt: debugConfig.returnRawPrompt,
      dry_run: debugConfig.dryRun,
    };
    if (debugConfig.model) debugOptions.model = debugConfig.model;
    if (debugConfig.temperature > 0)
      debugOptions.temperature = debugConfig.temperature;

    // Add Prompt Override
    if (debugConfig.systemPromptOverride.trim()) {
      debugOptions.system_prompt_override = debugConfig.systemPromptOverride;
    }

    // Add Context Injection
    if (debugConfig.injectedContext.length > 0) {
      const contextMap: Record<string, string> = {};
      debugConfig.injectedContext.forEach((item) => {
        if (item.key.trim()) contextMap[item.key] = item.value;
      });
      if (Object.keys(contextMap).length > 0) {
        debugOptions.injected_context = contextMap;
      }
    }

    const response = await fetch("/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": localStorage.getItem("api_key") || "",
      },
      body: JSON.stringify({
        messages: messages.value
          .filter((m) => !m.isThinking && m.content)
          .map((m) => ({
            role: m.role === "agent" ? "assistant" : m.role,
            content: m.content,
          })),
        stream: true,
        enable_multi_agent: debugConfig.enableMultiAgent,
        debug_options: debugOptions,
        agent_id: agentParams.agent_id,
        version_id: agentParams.version_id,
        conversation_id: conversationId.value,
      }),
      signal: abortController.signal,
    });

    if (!response.ok) {
      let errorDetails = `Status: ${response.status} ${response.statusText}`;
      try {
        const errData = await response.json();
        if (errData.message) errorDetails = errData.message;
      } catch (e) {}
      throw new Error(errorDetails);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) throw new Error("No response body");

    const sseLineParser = createSseLineParser();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      const dataLines = sseLineParser.feed(chunk);

      for (const dataStr of dataLines) {
          if (dataStr === "[DONE]") continue;

          try {
            const data = JSON.parse(dataStr);

            // Handle Trace ID
            if (data.trace_id) {
              agentMsg.value.trace_id = data.trace_id;
            }
            if (data.data && data.data.trace_id) {
              agentMsg.value.trace_id = data.data.trace_id;
            }

            // Handle Structured Logs
            if (data.type === "log") {
              addRealLog(agentMsg.value, data);
            }
            // Handle Router Logic (New)
            else if (data.type === "router_log") {
              const thoughtText = data.thought || "No reasoning provided.";
              const agentName = data.selected_agent || "Unknown";
              const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";
              
              addRealLog(agentMsg.value, {
                title: "智能路由决策",
                details: `**思考过程 (Chain of Thought):**\n${thoughtText}\n\n**最终选择:** ${agentName} ${conf}`,
                status: "success",
                isDebug: true,
                isRouter: true // Mark as router log
              });
            }
            // Handle Debug Events
            else if (data.type === "debug") {
              if (data.subtype === "raw_prompt") {
                agentMsg.value.rawPrompt = data.data;
                addRealLog(agentMsg.value, {
                  title: "Debug: Raw Prompt Captured",
                  details: 'Click "Raw Prompt" button to view.',
                  status: "success",
                  isDebug: true,
                });
              }
            }
            // Handle Citations (New)
            else if (data.type === "citation") {
              if (data.data && Array.isArray(data.data)) {
                if (!agentMsg.value.citations) agentMsg.value.citations = [];
                
                data.data.forEach((newRef: any) => {
                    const exists = agentMsg.value.citations?.some(c => c.chunk_id === newRef.chunk_id || (c.content === newRef.content && c.doc_name === newRef.doc_name));
                    if (!exists) {
                        agentMsg.value.citations?.push(newRef);
                    }
                });
              }
            }
            // Handle Context Update Events (New)
            else if (data.type === "context") {
              addRealLog(agentMsg.value, {
                title: "✨ Context Updated",
                details: JSON.stringify(data.data, null, 2),
                status: "success",
              });
              // Update reactive context stack
              if (data.data) {
                agentContext.value = { ...agentContext.value, ...data.data };
              }
            }
            // Handle Thinking State
            else if (data.type === "thinking") {
              // Maintain thinking state when receiving thinking continuation signals
              if (data.status === "continuing") {
                agentMsg.value.isThinking = true;
              }
            }
            // Handle Content Stream
            else if (data.content) {
              // --- [SMART TIMER STOP] ---
              // Only stop thinking if we actually started receiving the final answer content.
              const isRealContent = !data.content.includes('<function_calls') && !data.content.includes('<think');
              
              if (isRealContent) {
                // Auto-collapse thought process on first content token
                if (agentMsg.value.isThinking && agentMsg.value.isThoughtExpanded) {
                   agentMsg.value.isThoughtExpanded = false;
                }
                
                agentMsg.value.content += data.content;
                
                if (agentMsg.value.isThinking) {
                  agentMsg.value.isThinking = false;
                  if (thoughtTimer) {
                    clearInterval(thoughtTimer);
                    thoughtTimer = null;
                  }
                }
              }
            }
            // Handle Meta Info (Agent Name)
            else if (data.type === "meta") {
              if (data.agent_name) {
                agentMsg.value.agentName = data.agent_name;
                if (data.agent_display_name) {
                    agentMsg.value.agentDisplayName = data.agent_display_name;
                }
              }
            }

            // Handle Status
            if (data.status === "generating") {
              // Only stop thinking if we already have content, otherwise wait for first content chunk
              if (agentMsg.value.content) {
                 agentMsg.value.isThinking = false;
                 if (thoughtTimer) {
                   clearInterval(thoughtTimer);
                   thoughtTimer = null;
                 }
              }
            } else if (data.status === "error") {
              agentMsg.value.isThinking = false;
              if (thoughtTimer) {
                clearInterval(thoughtTimer);
                thoughtTimer = null;
              }
            }
            if (data.intent) {
              agentMsg.value.intent = data.intent;
            }
          } catch (e) {
            console.warn("Failed to parse SSE chunk", e);
          }
      }
    }

    // Render final content as markdown - REMOVED to fix Chart Rendering
    // agentMsg.value.content = renderMarkdown(agentMsg.value.content);
    agentMsg.value.isThinking = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }

    // Final cleanup of pending logs
    if (agentMsg.value.logs) {
      agentMsg.value.logs.forEach(log => {
        if (log.status === 'pending') {
          log.status = 'success'; // Assume success if stream finished normally
        }
      });
    }

    // Refresh history sidebar
    fetchHistory();
  } catch (error: any) {
    if (error.name === "AbortError") return;

    agentMsg.value.content += `\n[异常中断: ${error.message}]`;
    agentMsg.value.isThinking = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }
    
    // Mark pending logs as error
    if (agentMsg.value.logs) {
      agentMsg.value.logs.forEach(log => {
        if (log.status === 'pending') {
          log.status = 'error';
        }
      });
    }
  } finally {
    isProcessing.value = false;
    nextTick(() => {
      if (inputTextarea.value) inputTextarea.value.focus();
    });
  }
};

const addRealLog = (msg: Message, data: any) => {
  if (!msg.logs) msg.logs = [];
  const logId = data.id || Date.now() + Math.random();
  const existingLog = msg.logs.find((l) => l.id === logId);

  if (existingLog) {
    existingLog.status = data.status || "success";
    existingLog.title = data.title || existingLog.title;
    existingLog.details = data.details || existingLog.details;
    if (data.isDebug !== undefined) existingLog.isDebug = data.isDebug;
    if (data.isRouter !== undefined) existingLog.isRouter = data.isRouter;
    if (data.category !== undefined) existingLog.category = data.category as any;
  } else {
    // Categorization Logic for new logs
    let inferredCategory = (data.category as any) || 'default';
    if (inferredCategory === 'default') {
      const titleLower = (data.title || "").toLowerCase();
      if (titleLower.includes("路由") || titleLower.includes("router")) inferredCategory = 'router';
      else if (titleLower.includes("sql") || titleLower.includes("数据")) inferredCategory = 'sql';
      else if (titleLower.includes("knowledge") || titleLower.includes("知识") || titleLower.includes("检索") || titleLower.includes("分析")) inferredCategory = 'knowledge';
      else if (titleLower.includes("tool") || titleLower.includes("工具")) inferredCategory = 'tool';
      else if (titleLower.includes("intent") || titleLower.includes("意图")) inferredCategory = 'intent';
      else if (titleLower.includes("permission") || titleLower.includes("权限")) inferredCategory = 'permission';
    }

    const log: LogEntry = {
      id: logId,
      title: data.title || "Processing",
      details: data.details || "",
      status: data.status || "success",
      isExpanded: false,
      isDebug: data.isDebug,
      isRouter: data.isRouter,
      category: inferredCategory,
      model: data.model,
      temperature: data.temperature
    };
    msg.logs.push(log);
  }
};

const toggleLog = (log: LogEntry) => {
  log.isExpanded = !log.isExpanded;
};

// Simple Table Renderer Helper
const tryRenderTable = (text: string) => {
  try {
    if (!text.trim().startsWith("[")) return null;
    const data = JSON.parse(text);
    if (Array.isArray(data) && data.length > 0 && typeof data[0] === "object") {
      const cols = Object.keys(data[0]);
      return { cols, rows: data };
    }
  } catch (e) {
    return null;
  }
  return null;
};

const tryRenderCitations = (text: string) => {
  try {
    const data = JSON.parse(text);
    // Check if it looks like RAGFlow references (array of objects with doc_name/content)
    if (Array.isArray(data) && data.length > 0 && (data[0].doc_name || data[0].content)) {
        return data;
    }
  } catch (e) {
    return null;
  }
  return null;
};

onUnmounted(() => {
  if (abortController) abortController.abort();
});
</script>

<template>
  <div class="h-full flex bg-gray-100 overflow-hidden">
    <!-- Trace Log Viewer Component -->
    <TraceLogViewer
      :visible="showFullLogViewer"
      :trace-id="activeTraceId"
      @close="showFullLogViewer = false"
    />

    <!-- Session Preview Modal (New Feature) -->
    <div
      v-if="showSessionPreview"
      class="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm sm:p-4"
      @click.self.stop="showSessionPreview = false"
    >
        <div class="bg-white dark:bg-gray-800 w-full flex flex-col overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700 shadow-2xl transition-all duration-300 max-w-3xl h-[80vh] rounded-xl">
            <!-- Header -->
            <div class="px-4 py-3 sm:px-6 sm:py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50 flex-shrink-0">
                <div class="flex items-center gap-3">
                    <h3 class="text-sm sm:text-lg font-black text-gray-800 dark:text-gray-100 truncate">会话概览 (Session Preview)</h3>
                    <span v-if="conversationTurns.length" class="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-bold">{{ conversationTurns.length }} Rounds</span>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                    <button 
                        v-if="conversationTurns.length > 0"
                        @click.stop="continueChatFromTrace"
                        class="flex items-center space-x-1.5 px-3 py-1.5 bg-primary/10 text-primary hover:bg-primary hover:text-white rounded-lg transition-all text-xs font-black border border-primary/20"
                    >
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
                        <span>继续调试</span>
                    </button>
                    <div class="w-px h-4 bg-gray-300 dark:bg-gray-600 mx-1"></div>
                    <button @click.stop="showSessionPreview = false" class="p-2 text-gray-500 hover:text-gray-800"><svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg></button>
                </div>
            </div>
            
            <!-- Content -->
            <div class="flex-1 overflow-y-auto p-4 sm:p-6 bg-gray-50 dark:bg-gray-900 custom-scrollbar">
                <div v-if="loadingTrace" class="flex flex-col items-center justify-center h-full text-gray-400 py-20">
                    <svg class="w-10 h-10 animate-spin mb-3 text-primary" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <p class="text-xs font-bold uppercase tracking-widest">加载会话记录...</p>
                </div>
                <div v-else-if="conversationTurns.length > 0" class="space-y-6 pb-10">
                    <div v-for="(turn, tIdx) in conversationTurns" :key="turn.id" 
                         class="bg-white dark:bg-gray-800 p-4 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm relative overflow-hidden"
                         :class="{'ring-2 ring-primary/20': turn.trace_id === activeTraceId}"
                    >
                        <div class="flex justify-between items-center mb-4">
                            <span class="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center gap-1.5">
                                <span class="w-2 h-2 rounded-full bg-blue-500"></span>
                                回合 #{{ tIdx + 1 }}
                            </span>
                            <span class="text-[9px] text-gray-400 font-mono">{{ formatDate(turn.created_at) }}</span>
                        </div>
                        <div class="space-y-4">
                            <div>
                                <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1 opacity-70">用户</div>
                                <div class="text-gray-800 dark:text-gray-200 text-sm font-bold bg-gray-50 p-2 rounded-lg border border-gray-100">{{ turn.query }}</div>
                            </div>
                            <div>
                                <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1 opacity-70">智能体</div>
                                <div class="text-gray-600 dark:text-gray-300 text-xs sm:text-sm"><MessageRenderer :content="turn.summary" /></div>
                            </div>
                        </div>
                        
                        <!-- Actions & Steps -->
                        <div class="mt-4 pt-3 border-t border-gray-50">
                            <div class="flex items-center justify-between">
                                <button @click="toggleTurnSteps(turn)" class="flex items-center p-2 rounded-lg hover:bg-gray-50 transition-all group">
                                    <span class="text-[10px] font-black text-gray-500 group-hover:text-primary transition-colors uppercase tracking-widest">执行步骤 (Steps)</span>
                                    <div class="flex items-center ml-2">
                                        <div v-if="turn.loading" class="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2"></div>
                                        <svg class="w-4 h-4 text-gray-400 transform transition-transform duration-300" :class="{ 'rotate-180': turn.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" /></svg>
                                    </div>
                                </button>
                                
                                <!-- Link to Full Trace Viewer -->
                                <button 
                                    @click.stop="openFullLogs(turn.trace_id)"
                                    class="px-3 py-1.5 text-[10px] font-bold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded border border-indigo-100 transition-colors uppercase tracking-widest flex items-center gap-1"
                                >
                                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg>
                                    完整链路
                                </button>
                            </div>
                            
                            <div v-show="turn.isExpanded" class="mt-3 pl-4 border-l-2 border-gray-100 space-y-3 animate-fade-in">
                                <div v-if="turn.steps && turn.steps.length > 0" class="space-y-3">
                                    <div v-for="(step, sIdx) in turn.steps" :key="sIdx" class="bg-white p-3 rounded-xl border border-gray-100 shadow-sm text-xs">
                                        <div class="flex justify-between items-center mb-2">
                                            <span class="text-[9px] font-black px-1.5 py-0.5 rounded uppercase bg-blue-100 text-blue-700">{{ step.event_type }}</span>
                                            <span class="text-[8px] text-gray-400 font-mono">{{ step.execution_time_ms?.toFixed(0) }}ms</span>
                                        </div>
                                        <div v-if="step.tool_output?.content" class="text-gray-600 leading-relaxed break-words">{{ step.tool_output.content.slice(0, 300) + (step.tool_output.content.length > 300 ? '...' : '') }}</div>
                                    </div>
                                </div>
                                <div v-else-if="!turn.loading" class="py-2 text-center text-[10px] text-gray-400 italic">暂无记录</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Left Sidebar: History -->
    <ChatHistorySidebar
      v-model:visible="showHistorySidebar"
      v-model="historyKeyword"
      :loading="loadingHistory"
      :history-list="aggregatedHistoryList"
      :active-trace-id="activeTraceId"
      @fetch-history="fetchHistory"
      @load-chat="openSessionPreview" 
      @open-full-logs="openSessionPreview"
    />

    <!-- Center: Main Chat Area -->
    <div class="flex-1 flex flex-col bg-white shadow-sm overflow-hidden mr-px">
      <!-- Header -->
      <div
        class="h-14 px-6 border-b border-gray-200 flex items-center justify-between bg-white flex-shrink-0"
      >
        <div class="flex items-center">
          <button
            @click="showHistorySidebar = !showHistorySidebar"
            class="mr-3 p-1.5 rounded-md transition-all flex items-center space-x-1.5"
            :class="
              !showHistorySidebar
                ? 'bg-primary/10 text-primary px-3 border border-primary/20 hover:bg-primary/20'
                : 'text-gray-500 hover:bg-gray-100'
            "
            :title="showHistorySidebar ? '收起历史' : '展开历史'"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                v-if="showHistorySidebar"
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M11 19l-7-7 7-7m8 14l-7-7 7-7"
              />
              <path
                v-else
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M13 5l7 7-7 7M5 5l7 7-7 7"
              />
            </svg>
            <span v-if="!showHistorySidebar" class="text-xs font-bold"
              >历史记录</span
            >
          </button>
          <svg
            class="w-5 h-5 text-primary mr-2"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"
            />
          </svg>
          <span class="font-medium text-gray-800"
            >智能体调试 (Real-Time Agent)</span
          >
        </div>
        <div class="flex items-center space-x-4">
          <button
            @click="clearHistory"
            class="text-gray-500 hover:text-red-600 transition-colors flex items-center space-x-1"
            title="清空会话"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
              />
            </svg>
            <span class="text-xs font-medium">清空</span>
          </button>
          <div class="h-4 w-px bg-gray-200"></div>
          <button
            @click="showLogicFlowModal = true"
            class="text-gray-500 hover:text-blue-600 transition-colors flex items-center space-x-1"
            title="查看运行逻辑图"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 20l-5.447-2.724A2 2 0 013 15.492V4.508a2 2 0 011.553-1.944L9 2l6 2.724a2 2 0 011 1.732v10.984a2 2 0 01-1.553 1.944L9 20z"
              />
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 2v18M15 4v18"
              />
            </svg>
            <span class="text-xs font-medium">运行逻辑</span>
          </button>
          <div class="h-4 w-px bg-gray-200"></div>
          <button
            @click="showConfigPanel = !showConfigPanel"
            class="p-1.5 rounded-md transition-colors"
            :class="
              showConfigPanel
                ? 'bg-primary/10 text-primary'
                : 'text-gray-500 hover:bg-gray-100'
            "
            :title="showConfigPanel ? '收起配置' : '展开配置'"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
              />
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
              />
            </svg>
          </button>
          
          <div class="h-4 w-px bg-gray-200"></div>
          
          <button
            @click="exportChat"
            class="p-1.5 rounded-md text-gray-500 hover:bg-gray-100 transition-colors"
            title="导出对话 (Markdown)"
          >
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Mode Selector Bar -->
      <div
        class="px-6 py-2 bg-gray-50 border-b border-gray-200 flex items-center space-x-4"
      >
        <!-- Mode Toggle -->
        <div class="flex bg-gray-200 p-1 rounded-lg">
          <button
            @click="
              debugMode = 'auto';
              agentParams.agent_id = null;
            "
            class="px-3 py-1.5 text-xs font-medium rounded-md transition-all"
            :class="
              debugMode === 'auto'
                ? 'bg-white text-primary shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            "
          >
            🤖 自动路由 (Auto)
          </button>
          <button
            @click="
              debugMode = 'specific';
              if (agents.length) agentParams.agent_id = agents[0].id;
            "
            class="px-3 py-1.5 text-xs font-medium rounded-md transition-all"
            :class="
              debugMode === 'specific'
                ? 'bg-white text-primary shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            "
          >
            🎯 指定智能体 (Specific)
          </button>
        </div>

        <!-- Agent Dropdown (Visible only in Specific Mode) -->
        <div v-if="debugMode === 'specific'" class="relative w-48 z-10">
          <select
            v-model="agentParams.agent_id"
            class="block w-full pl-3 pr-10 py-1.5 text-xs border-gray-300 focus:outline-none focus:ring-primary focus:border-primary sm:text-sm rounded-md shadow-sm"
          >
            <option v-for="agent in agents" :key="agent.id" :value="agent.id">
              {{ agent.is_system ? '🔒' : '👤' }} {{ agent.display_name }} ({{ agent.name }})
            </option>
          </select>
        </div>

        <div class="text-xs text-gray-400 border-l pl-3 ml-2">
          {{
            debugMode === "auto"
              ? "系统将根据您的问题自动选择最合适的 Agent"
              : "强制请求发送给当前选中的 Agent"
          }}
        </div>
      </div>

      <!-- Chat History -->
      <div
        ref="messagesContainer"
        @scroll="handleScroll"
        class="flex-1 overflow-y-auto p-6 space-y-6 bg-gray-50 custom-scrollbar"
      >
        <div
          v-for="msg in displayMessages"
          :key="msg.id"
          class="flex w-full"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <!-- User Message -->
          <div
            v-if="msg.role === 'user'"
            class="flex space-x-3 items-start flex-row-reverse space-x-reverse max-w-[85%] group"
          >
            <div
              class="w-10 h-10 rounded-full bg-gray-200 flex-shrink-0 flex items-center justify-center text-gray-500 shadow-sm border border-white"
            >
              <svg
                class="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="1.5"
                  d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                />
              </svg>
            </div>
            
            <!-- Editing Mode -->
            <div v-if="editingMsgId === msg.id" class="w-full flex flex-col items-end space-y-2">
               <textarea 
                  v-model="editContent"
                  class="w-full p-3 border border-primary/30 rounded-lg shadow-sm focus:ring-2 focus:ring-primary focus:border-primary text-sm min-h-[80px]"
                ></textarea>
                <div class="flex space-x-2">
                  <button 
                    @click="cancelEdit" 
                    class="px-3 py-1 text-xs text-gray-500 bg-gray-100 hover:bg-gray-200 rounded"
                  >取消</button>
                  <button 
                    @click="saveAndResend" 
                    class="px-3 py-1 text-xs text-white bg-primary hover:bg-primary-dark rounded"
                  >发送</button>
                </div>
            </div>

            <!-- Normal Mode -->
            <div v-else class="flex flex-col items-end">
              <div
                class="bg-primary text-white px-5 py-3.5 rounded-2xl rounded-tr-none shadow-sm text-sm leading-relaxed text-left relative"
              >
                 <MessageRenderer :content="msg.content" />
              </div>
              <!-- User Actions -->
              <div
                class="flex items-center space-x-2 mt-1 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <!-- Edit Button -->
                <button
                  @click="startEdit(msg)"
                  class="text-xs text-gray-400 hover:text-gray-600 flex items-center space-x-1 transition-colors"
                  :disabled="isProcessing"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                     <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                  <span>编辑</span>
                </button>
                
                <button
                  @click="copyContent(msg.content, $event)"
                  class="text-xs text-gray-400 hover:text-gray-600 flex items-center space-x-1 transition-colors"
                >
                  <svg
                    class="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                    />
                  </svg>
                  <span>复制</span>
                </button>
              </div>
            </div>
          </div>

          <!-- System Message / Separator -->
          <div
            v-else-if="msg.role === 'system'"
            class="w-full flex flex-col items-center justify-center my-6"
          >
             <span v-if="msg.timestamp" class="text-xs text-gray-400 dark:text-gray-500 font-medium tracking-wide mb-2">{{ msg.timestamp }}</span>
            <div class="flex items-center space-x-3 opacity-60">
              <div class="h-px w-16 bg-gray-300 dark:bg-gray-600"></div>
              <span class="text-xs text-gray-400 dark:text-gray-500 font-medium tracking-wide">{{ msg.content }}</span>
              <div class="h-px w-16 bg-gray-300 dark:bg-gray-600"></div>
            </div>
          </div>

          <!-- Agent Message -->
          <div v-else class="flex space-x-3 items-start max-w-[90%] group">
            <div
              class="w-10 h-10 rounded-full bg-blue-600 flex-shrink-0 flex items-center justify-center text-white shadow-md border border-blue-100"
            >
              <svg
                class="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="1.5"
                  d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"
                />
              </svg>
            </div>
            <div class="flex-1 space-y-2 min-w-0">
              <!-- Actions (Unified Toolbar) -->
              <div v-if="!msg.isGreeting && !msg.isHistory" class="flex flex-wrap items-center gap-2 mb-2">
                <!-- Agent Badge (Smart Routing State) -->
                <div
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium rounded-md select-none transition-all duration-300 border"
                  :class="msg.agentName 
                    ? 'text-blue-600 bg-blue-50 border-blue-100' 
                    : 'text-gray-400 bg-gray-50 border-gray-100 italic animate-pulse'"
                >
                  <svg
                    class="w-2.5 h-2.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"
                    />
                  </svg>
                  <span>{{ msg.agentDisplayName ? `${msg.agentDisplayName} · 正在服务` : (msg.agentName || '智能调度中...') }}</span>
                </div>

                <!-- Full Logs -->
                <button
                  v-if="msg.trace_id && !msg.isThinking"
                  @click="openFullLogs(msg.trace_id)"
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-100 rounded-md transition-colors"
                >
                  <svg
                    class="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"
                    />
                  </svg>
                  <span>完整日志</span>
                </button>

                <!-- Prompt -->
                <button
                  v-if="msg.rawPrompt"
                  @click="openRawPrompt(msg)"
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium text-purple-600 bg-purple-50 hover:bg-purple-100 border border-purple-100 rounded-md transition-colors"
                  title="View Raw Prompt"
                >
                  <svg
                    class="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"
                    />
                  </svg>
                  <span>Prompt</span>
                </button>

                <!-- Regenerate Button -->
                <button
                  v-if="messages.indexOf(msg) === messages.length - 1 && !isProcessing && !msg.isThinking"
                  @click="regenerate(msg)"
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium text-gray-500 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-md transition-colors"
                  title="重新生成"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  <span>重新生成</span>
                </button>
              </div>

              <!-- Agent Message Bubble (Unified Card Style) -->
              <div 
                v-if="(!msg.isGreeting && (msg.logs && msg.logs.length > 0)) || msg.content || (msg.citations && msg.citations.length > 0)"
                class="bg-gradient-to-br from-slate-50/80 to-white dark:from-slate-900/20 dark:to-gray-800 rounded-2xl rounded-tl-none border border-gray-200 dark:border-gray-700 border-l-4 border-l-primary/60 dark:border-l-primary/40 shadow-sm p-4 overflow-hidden"
              >
              <!-- Logs (Collapsible Thought Accordion) -->
              <div v-if="!msg.isGreeting && msg.logs && msg.logs.length > 0" class="mb-3">
                <!-- Header -->
                <button
                  @click="msg.isThoughtExpanded = !msg.isThoughtExpanded"
                  class="flex items-center space-x-2 w-full text-left px-3 py-2 rounded-lg hover:bg-gray-100 transition-colors group/header select-none border border-transparent hover:border-gray-200"
                >
                  <!-- Icon -->
                  <div class="flex-shrink-0 flex items-center justify-center w-5 h-5 rounded bg-gray-100 text-gray-500">
                    <svg v-if="msg.isThinking" class="w-3.5 h-3.5 animate-spin text-primary" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <svg v-else class="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
                  </div>

                  <!-- Title & Meta -->
                  <div class="flex-1 flex items-center justify-between min-w-0">
                    <div class="flex items-center space-x-2 overflow-hidden">
                      <span class="text-xs font-semibold text-gray-700 truncate">
                        {{ msg.isThinking ? '思考中...' : '深度思考过程' }}
                      </span>
                      <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 font-mono">
                        {{ msg.logs.length }} 步骤
                      </span>
                    </div>
                    <span class="text-[10px] text-gray-400 font-mono ml-2 flex-shrink-0">
                      {{ msg.thoughtDuration ? `${msg.thoughtDuration}s` : '' }}
                    </span>
                  </div>

                  <!-- Chevron -->
                  <svg
                    class="w-4 h-4 text-gray-400 transform transition-transform duration-200"
                    :class="{ 'rotate-180': msg.isThoughtExpanded }"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>

                <!-- Body -->
                <transition
                  enter-active-class="transition-all duration-300 ease-out"
                  enter-from-class="opacity-0 max-h-0"
                  enter-to-class="opacity-100 max-h-[500px]"
                  leave-active-class="transition-all duration-200 ease-in"
                  leave-from-class="opacity-100 max-h-[500px]"
                  leave-to-class="opacity-0 max-h-0"
                >
                  <div
                    v-show="msg.isThoughtExpanded"
                    class="overflow-hidden"
                  >
                    <div class="relative ml-2 pl-4 py-2 space-y-3 border-l-2 border-gray-100">
                      <div
                        v-for="(log, idx) in msg.logs"
                        :key="log.id"
                        class="relative group/log"
                      >
                        <!-- Timeline Numbered Badge -->
                        <div class="absolute -left-[26px] top-1.5 w-5 h-5 rounded-full border border-gray-200 dark:border-gray-700 shadow-sm flex items-center justify-center text-[10px] font-black text-white group-hover/log:scale-110 transition-all z-10 select-none"
                             :class="{
                               'bg-blue-500': log.category === 'router',
                               'bg-violet-500': log.category === 'intent',
                               'bg-yellow-500': log.category === 'sql',
                               'bg-amber-500': log.category === 'knowledge',
                               'bg-indigo-500': log.category === 'tool',
                               'bg-emerald-500': log.category === 'permission',
                               'bg-gray-500': !log.category || log.category === 'default',
                               'animate-pulse': log.status === 'pending'
                             }"
                        >
                          {{ Number(idx) + 1 }}
                        </div>

                        <!-- Log Card -->
                        <div 
                            class="rounded-md border p-2.5 text-xs transition-all cursor-pointer hover:shadow-sm"
                            :class="{
                               'bg-blue-50/50 border-blue-100': log.category === 'router',
                               'bg-violet-50/50 border-violet-100': log.category === 'intent',
                               'bg-yellow-50/50 border-yellow-100': log.category === 'sql',
                               'bg-amber-50/50 border-amber-100': log.category === 'knowledge',
                               'bg-indigo-50/50 border-indigo-100': log.category === 'tool',
                               'bg-emerald-50/50 border-emerald-100': log.category === 'permission',
                               'bg-white border-gray-200': !log.category || log.category === 'default'
                            }"
                            @click="toggleLog(log)"
                        >
                            <!-- Card Header -->
                            <div class="flex items-start justify-between gap-2">
                                <div class="flex-1 min-w-0">
                                    <div class="flex items-center gap-1.5 flex-wrap">
                                        <!-- Title -->
                                        <span class="font-medium text-gray-700 flex items-center gap-1">
                                            <span>{{ log.title }}</span>
                                            <span v-if="log.status === 'pending'" class="text-[10px] text-gray-400 animate-pulse">...</span>
                                        </span>
                                        
                                        <!-- Category Badge -->
                                        <span v-if="log.category && log.category !== 'default'" class="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider text-white shadow-sm"
                                          :class="{
                                            'bg-blue-500': log.category === 'router',
                                            'bg-violet-500': log.category === 'intent',
                                            'bg-yellow-500': log.category === 'sql',
                                            'bg-amber-500': log.category === 'knowledge',
                                            'bg-indigo-500': log.category === 'tool',
                                            'bg-emerald-500': log.category === 'permission'
                                          }"
                                        >
                                          {{ log.category }}
                                        </span>

                                        <!-- Model Info Badge (Debug Only) -->
                                        <span 
                                          v-if="log.model"
                                          class="px-1.5 py-0.5 rounded bg-gray-100 border border-gray-200 text-[9px] text-gray-500 font-mono flex items-center"
                                          :title="`执行模型: ${log.model} / Temp: ${log.temperature ?? 'N/A'}`"
                                        >
                                          <svg class="w-2.5 h-2.5 mr-1 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
                                          </svg>
                                          {{ log.model }}
                                        </span>
                                    </div>
                                </div>
                                
                                <!-- Chevron & Copy Actions -->
                                <div class="flex items-center gap-2">
                                  <button 
                                    v-if="log.details && log.isExpanded"
                                    @click.stop="copyContent(log.details, $event)"
                                    class="p-1 text-gray-400 hover:text-primary transition-all rounded hover:bg-white border border-transparent hover:border-gray-100 shadow-none hover:shadow-sm"
                                    title="复制详情"
                                  >
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                                    </svg>
                                  </button>
                                  <svg v-if="log.details" class="w-3 h-3 text-gray-400 flex-shrink-0 transition-transform" :class="{ 'rotate-180': log.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" /></svg>
                                </div>
                            </div>

                            <!-- Card Body (Details) -->
                            <div v-show="log.isExpanded" class="mt-2 pt-2 border-t border-gray-100">
                                 <!-- 1. Citation Rendering -->
                                <div v-if="tryRenderCitations(log.details)" class="space-y-2">
                                    <div v-for="(ref, idx) in tryRenderCitations(log.details)" :key="idx" class="p-2 bg-blue-50/30 rounded border border-blue-100/50 text-xs">
                                        <div class="font-bold text-blue-700 mb-1 flex justify-between items-center">
                                            <div class="flex items-center space-x-1">
                                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                                                <span class="truncate max-w-[150px]">{{ ref.doc_name || 'Unknown Document' }}</span>
                                            </div>
                                            <span v-if="ref.similarity" class="bg-white px-1.5 py-0.5 rounded text-[10px] text-blue-600 font-medium border border-blue-100 shadow-sm">{{ (ref.similarity * 100).toFixed(0) }}%</span>
                                        </div>
                                        <div class="text-gray-600 leading-relaxed" :title="ref.content">
                                            {{ ref.content }}
                                        </div>
                                    </div>
                                </div>

                                <!-- 2. Auto Table Rendering -->
                                <div v-else-if="tryRenderTable(log.details)" class="overflow-x-auto border rounded-lg bg-white">
                                    <table class="min-w-full text-xs text-left text-gray-500">
                                        <thead class="bg-gray-50 text-gray-700 uppercase font-bold">
                                            <tr>
                                                <th v-for="col in tryRenderTable(log.details)?.cols" :key="col" class="px-3 py-2 border-b border-gray-200 whitespace-nowrap">{{ col }}</th>
                                            </tr>
                                        </thead>
                                        <tbody class="divide-y divide-gray-100">
                                            <tr v-for="(row, idx) in tryRenderTable(log.details)?.rows" :key="idx" class="hover:bg-gray-50/50">
                                                <td v-for="col in tryRenderTable(log.details)?.cols" :key="col" class="px-3 py-2 whitespace-nowrap font-mono text-gray-600">{{ (row as any)[col] }}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>

                                <!-- 3. SQL Detection & Pretty Print -->
                                <div v-else-if="log.details && (log.details.includes('SELECT ') || log.details.includes('[Executed SQL]:') || log.details.includes('SQL:'))" class="space-y-1.5">
                                    <div class="p-2 bg-gray-900 rounded border border-gray-800 font-mono text-[10px] text-emerald-400 leading-relaxed overflow-x-auto relative group/sql">
                                        <div class="flex justify-between items-center mb-1 text-[9px] text-gray-500 font-sans uppercase tracking-tight">
                                          <span>SQL Query</span>
                                          <button @click.stop="copyContent(log.details, $event)" class="text-gray-600 hover:text-emerald-400 transition-colors uppercase">Copy</button>
                                        </div>
                                        <pre class="whitespace-pre-wrap break-all">{{ log.details }}</pre>
                                    </div>
                                </div>

                                <!-- 4. Default JSON/Text -->
                                <pre v-else class="font-mono text-[10px] text-gray-500 whitespace-pre-wrap break-all leading-relaxed">{{ log.details }}</pre>
                            </div>
                        </div>
                      </div>

                      <!-- Dynamic Next Step Indicator -->
                      <div
                        v-if="msg.isThinking"
                        class="pl-1 pb-1 pt-1 mt-2"
                      >
                        <div class="flex items-center space-x-3 text-[11px] text-gray-500 font-medium">
                          <div class="relative flex h-2 w-2 -left-[5px]">
                            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-40"></span>
                            <span class="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
                          </div>
                          <span class="animate-pulse tracking-wide italic">
                            {{ msg.logs && msg.logs.length > 0 ? '正在执行下一步...' : '正在初始化...' }}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </transition>
              </div>
              <!-- Close the v-if="msg.logs && msg.logs.length > 0" container -->

              <!-- Main Content -->
              <div
                v-if="msg.content"
                class="relative group/content mt-2 text-gray-800 leading-relaxed markdown-body"
              >
                <!-- Floating Copy Button -->
                <button
                  @click="copyContent(msg.content, $event)"
                  class="absolute -top-1 -right-1 p-1.5 text-gray-400 bg-white/90 dark:bg-gray-700/90 hover:bg-gray-100 dark:hover:bg-gray-600 hover:text-primary rounded-md transition-all opacity-0 group-hover/content:opacity-100 focus:opacity-100 z-10 shadow-sm border border-gray-100 dark:border-gray-600"
                  title="复制内容"
                >
                  <svg
                    class="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                    />
                  </svg>
                </button>
                <MessageRenderer :content="msg.content" @quick-question="handleQuickQuestion" @show-citation="(id) => handleShowCitation(msg, id)" />
                <!-- 导出 / 点赞踩（托管 RAGFlow、OpenClaw 不展示点赞踩） -->
                <div
                  v-if="msg.role === 'agent' && !msg.isThinking && (msg.trace_id || !hideDebugLikeDislikeForHostedAgent)"
                  class="flex items-center space-x-2 mt-2 pt-2 border-t border-gray-50 opacity-20 hover:opacity-100 group-hover/content:opacity-100 transition-opacity"
                  :class="{'!opacity-100': msg.feedback && !hideDebugLikeDislikeForHostedAgent}"
                >
                  <!-- Export Data Button -->
                  <button
                    v-if="msg.trace_id"
                    @click="exportData(msg.trace_id, 'xlsx')"
                    class="flex items-center space-x-1 p-1 rounded hover:bg-blue-50 text-gray-400 hover:text-primary transition-colors"
                    title="导出数据 (Excel)"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <span class="text-[10px] font-bold">导出</span>
                  </button>
                  <div v-if="msg.trace_id && !hideDebugLikeDislikeForHostedAgent" class="w-px h-3 bg-gray-200 mx-1"></div>
                  <button
                    v-if="!hideDebugLikeDislikeForHostedAgent"
                    @click="handleFeedback(msg, 'up')"

                    class="p-1 rounded hover:bg-green-50 text-gray-400 hover:text-green-500 transition-colors"
                    :class="{ 'text-green-500 bg-green-50': msg.feedback === 'up' }"
                    title="很有帮助"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.708C19.712 10 20.5 10.743 20.5 11.658c0 .354-.05.7-.145 1.03l-1.921 6.641C18.232 20.141 17.514 21 16.5 21H8.5c-1.105 0-2-.895-2-2v-8c0-.55.224-1.05.586-1.414l5-5c.381-.381 1-.381 1.381 0L14 5v5z" />
                    </svg>
                  </button>
                  <button
                    v-if="!hideDebugLikeDislikeForHostedAgent"
                    @click="handleFeedback(msg, 'down')"
                    class="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors"
                    :class="{ 'text-red-500 bg-red-50': msg.feedback === 'down' }"
                    title="回答不准确"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.292C4.288 14 3.5 13.257 3.5 12.342c0-.354.05-.7.145-1.03l1.921-6.641C6.768 3.859 7.486 3 8.5 3H16.5c1.105 0 2 .895 2 2v8c0 .55-.224 1.05-.586 1.414l-5 5c-.381.381-1 .381-1.381 0L10 19v-5z" />
                    </svg>
                  </button>
                </div>
                <!-- Typewriter Cursor -->
                <span 
                  v-if="msg.role === 'agent' && isProcessing && messages.indexOf(msg) === messages.length - 1 && !msg.isThinking" 
                  class="typing-cursor"
                ></span>
              </div>
              <!-- Citations Area (Always outside text content container) -->
              <div v-if="msg.citations && msg.citations.some(c => (c.similarity || 0) >= 0.5)" class="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700/50 relative z-10">
                <button @click="msg.isCitationsExpanded = !msg.isCitationsExpanded" class="flex items-center space-x-1.5 mb-2 w-full text-left group/cite-head">
                   <svg class="w-3.5 h-3.5 text-gray-400 group-hover/cite-head:text-primary transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>
                   <span class="text-[10px] font-bold text-gray-400 uppercase tracking-wider flex-1">引用来源 ({{ msg.citations.filter(c => (c.similarity || 0) >= 0.5).length }})</span>
                   <svg class="w-3.5 h-3.5 text-gray-400 transform transition-transform duration-200" :class="{ 'rotate-180': msg.isCitationsExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" /></svg>
                </button>
                <transition enter-active-class="transition-all duration-300 ease-out" enter-from-class="opacity-0 max-h-0" enter-to-class="opacity-100 max-h-[500px]" leave-active-class="transition-all duration-200 ease-in" leave-from-class="opacity-100 max-h-[500px]" leave-to-class="opacity-0 max-h-0">
                  <div v-show="msg.isCitationsExpanded" class="overflow-hidden">
                    <div class="flex flex-wrap gap-2 py-1">
                       <template v-for="(cite, cIdx) in msg.citations" :key="cIdx">
                         <div v-if="(cite.similarity || 0) >= 0.5" class="group/cite relative flex items-center space-x-2 px-2.5 py-1.5 bg-gray-50 dark:bg-gray-800/80 border border-gray-100 dark:border-gray-700 rounded-lg hover:border-primary/40 dark:hover:border-primary/40 transition-all cursor-pointer overflow-hidden" @click="cite.isExpanded = !cite.isExpanded">
                            <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                            <span class="text-[11px] font-medium text-gray-600 dark:text-gray-300 truncate max-w-[150px]">{{ cite.doc_name }}</span>
                            <span v-if="cite.similarity" class="text-[9px] font-mono text-gray-400 px-1 rounded bg-gray-100 dark:bg-gray-700">{{ (cite.similarity * 100).toFixed(0) }}%</span>
                         </div>
                         <Teleport to="body">
                           <div v-if="cite.isExpanded" class="fixed inset-0 z-[200] flex items-center justify-center p-6 bg-black/40 backdrop-blur-sm" @click.stop="cite.isExpanded = false">
                              <div class="bg-white dark:bg-gray-800 p-6 rounded-xl shadow-2xl max-w-3xl w-full max-h-[85vh] flex flex-col border border-gray-200 dark:border-gray-700 animate-fade-in-up" @click.stop>
                                 <div class="flex justify-between items-start mb-4">
                                    <div class="flex items-center gap-3">
                                       <div class="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                                          <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                                       </div>
                                       <div>
                                          <h4 class="text-sm font-bold text-gray-800 dark:text-gray-100">{{ cite.doc_name }}</h4>
                                          <div class="flex items-center gap-2 mt-0.5">
                                             <span class="text-[10px] text-gray-400 font-mono">匹配度: {{ (cite.similarity * 100).toFixed(1) }}%</span>
                                             <span class="w-1 h-1 rounded-full bg-gray-300"></span>
                                             <span class="text-[10px] text-gray-400">ID: {{ cite.id }}</span>
                                          </div>
                                       </div>
                                    </div>
                                    <button @click="cite.isExpanded = false" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-gray-400">
                                       <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
                                    </button>
                                 </div>
                                 <div class="flex-1 overflow-y-auto bg-gray-50 dark:bg-gray-900/50 p-4 rounded-lg border border-gray-100 dark:border-gray-700/50 text-sm text-gray-600 dark:text-gray-300 leading-relaxed whitespace-pre-wrap font-sans italic">
                                    “{{ cite.content }}”
                                 </div>
                                 <div class="mt-4 flex justify-end">
                                    <button @click="cite.isExpanded = false" class="px-4 py-2 bg-primary text-white text-xs font-bold rounded-lg hover:opacity-90 transition-all shadow-md shadow-primary/20">
                                       关闭
                                    </button>
                                 </div>
                              </div>
                           </div>
                         </Teleport>
                       </template>
                    </div>
                  </div>
                </transition>
              </div>


              
              
              <style scoped>
              .typing-cursor::after {
                content: '▋';
                animation: blink 1s step-start infinite;
                display: inline-block;
                margin-left: 2px;
                vertical-align: baseline;
                color: #2563eb; /* Primary blue */
              }
              @keyframes blink {
                50% { opacity: 0; }
              }
              </style>
              </div>

              <!-- Thinking Indicator (Removed in favor of dynamic timeline indicator) -->
            </div>
          </div>
        </div>
      </div>

      <!-- Input Area -->
      <div class="flex-shrink-0 p-4 bg-white border-t border-gray-200">
        <!-- Preset Questions (Collapsible) -->
        <div class="max-w-4xl mx-auto mb-2">
          <!-- Header / Toggle -->
          <div class="flex items-center justify-between mb-2">
            <div
              @click="showShortcuts = !showShortcuts"
              class="flex items-center space-x-1 cursor-pointer group select-none"
            >
              <span
                class="text-xs font-bold text-gray-500 group-hover:text-primary transition-colors"
                >⚡️ 快捷指令</span
              >
              <svg
                class="w-3 h-3 text-gray-400 group-hover:text-primary transition-all duration-300"
                :class="{ 'rotate-180': !showShortcuts }"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M19 9l-7 7-7-7"
                />
              </svg>
            </div>
          </div>

          <!-- Buttons Grid -->
          <transition
            enter-active-class="transition-all duration-300 ease-out"
            enter-from-class="opacity-0 -translate-y-2 max-h-0"
            enter-to-class="opacity-100 translate-y-0 max-h-[100px]"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 translate-y-0 max-h-[100px]"
            leave-to-class="opacity-0 -translate-y-2 max-h-0"
          >
            <div
              v-show="showShortcuts"
              class="flex flex-wrap gap-2 overflow-hidden pb-1"
            >
              <button
                v-for="q in slashCommands"
                :key="q.id"
                @click="
                  userInput = q.command;
                  sendMessage();
                "
                :disabled="isProcessing"
                class="px-3 py-1.5 text-xs font-medium border rounded-full transition-all duration-200 disabled:opacity-50 flex items-center space-x-1"
                :class="[
                   currentUser && q.created_by === currentUser.user_name 
                    ? 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100' 
                    : 'bg-gray-50 text-gray-600 border-gray-200 hover:bg-primary/5 hover:border-primary/30 hover:text-primary'
                ]"
              >
                <span>{{ q.label }}</span>
                <span v-if="currentUser && q.created_by === currentUser.user_name" class="ml-1 w-1.5 h-1.5 rounded-full bg-blue-400"></span>
              </button>

              <!-- Add Command Button (Small) -->
              <button
                @click="openCommandManager"
                class="px-2 py-1.5 text-xs text-gray-400 border border-dashed border-gray-300 rounded-full hover:border-primary hover:text-primary transition-colors"
                title="管理快捷指令"
              >
                +
              </button>
            </div>
          </transition>
        </div>

        <div
          class="relative max-w-4xl mx-auto ring-1 ring-gray-200 rounded-xl shadow-sm focus-within:ring-2 focus-within:ring-primary transition-all"
        >
          <MentionList
            ref="mentionListRef"
            :visible="showMentionList"
            :keyword="mentionKeyword"
            :agents="agents"
            :position="mentionPosition"
            @select="handleMentionSelect"
            @close="showMentionList = false"
          />
          <textarea
            ref="inputTextarea"
            v-model="userInput"
            @keydown="handleKeydown"
            @input="handleInput"
            placeholder="输入你的指令..."
            class="w-full py-3 pl-10 pr-12 bg-transparent border-none resize-none focus:ring-0 focus:outline-none text-gray-700 custom-scrollbar"
            rows="1"
            style="height: auto; min-height: 52px"
          ></textarea>
          
          <!-- Multimodal Upload Placeholder -->
          <button
            class="absolute bottom-2.5 left-2 p-1.5 text-gray-400 hover:text-primary transition-colors hover:bg-gray-100 rounded-lg flex items-center justify-center"
            title="上传图片 (即将上线)"
            @click="handleImageUpload"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          </button>
          <div class="absolute bottom-2 right-2 flex items-center space-x-2">
            <!-- Stop Button -->
            <button
              v-if="isProcessing"
              @click="stopGeneration"
              class="p-1.5 rounded-lg transition-colors flex items-center justify-center bg-red-100 text-red-600 hover:bg-red-200"
              title="停止生成"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>

            <!-- Send Button -->
            <button
              v-else
              @click="sendMessage"
              :disabled="!userInput.trim()"
              class="p-1.5 rounded-lg transition-colors flex items-center justify-center"
              :class="
                userInput.trim()
                  ? 'bg-primary text-white hover:bg-primary-dark shadow-sm'
                  : 'bg-gray-100 text-gray-400'
              "
            >
              <svg
                class="w-5 h-5 transform rotate-90"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Configuration Panel -->
    <DebugConfigPanel
      v-model:visible="showConfigPanel"
      v-model:is-floating="isConfigPanelFloating"
      :config="debugConfig"
      :models="availableModels"
      :agent-params="agentParams"
      :loading-config="loadingConfig"
      :agent-context="agentContext"
      @load-config="loadCurrentPrompt"
      @clear-context="clearContext"
    />

    <!-- Modal: Raw Prompt -->
    <div
      v-if="showRawPromptModal && selectedRawPrompt"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden animate-fade-in-up"
      >
        <div
          class="h-14 px-6 border-b border-gray-100 flex items-center justify-between bg-gray-50"
        >
          <h3 class="font-bold text-gray-800">Raw Prompt Data</h3>
          <button
            @click="closeModals"
            class="text-gray-500 hover:text-gray-700"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <div class="flex-1 overflow-auto p-0 bg-gray-900">
          <div
            v-for="(msg, idx) in selectedRawPrompt"
            :key="idx"
            class="border-b border-gray-700 p-4"
          >
            <div class="flex items-center mb-2">
              <span
                class="text-xs font-bold uppercase tracking-wider px-2 py-0.5 rounded"
                :class="{
                  'bg-green-900 text-green-300': msg.role === 'system',
                  'bg-blue-900 text-blue-300': msg.role === 'user',
                  'bg-purple-900 text-purple-300': msg.role === 'assistant',
                  'bg-yellow-900 text-yellow-300': msg.role === 'tool',
                }"
              >
                {{ msg.role }}
              </span>
            </div>
            <pre
              class="text-sm font-mono text-gray-300 whitespace-pre-wrap break-all"
              >{{ msg.content }}</pre
            >
            <div
              v-if="msg.tool_calls"
              class="mt-2 pl-4 border-l-2 border-yellow-700"
            >
              <p class="text-xs text-yellow-500 font-bold mb-1">Tool Calls:</p>
              <pre class="text-xs text-yellow-200 font-mono">{{
                JSON.stringify(msg.tool_calls, null, 2)
              }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Modal: Command Manager -->
    <div
      v-if="showCommandManager"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-2xl flex flex-col overflow-hidden animate-fade-in-up"
      >
        <div
          class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50"
        >
          <h3 class="font-bold text-gray-800">快捷指令管理</h3>
          <button
            @click="closeModals"
            class="text-gray-500 hover:text-gray-700"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        <div class="p-6 bg-white flex-1 overflow-y-auto">
          <!-- Form -->
          <div class="mb-6 p-4 bg-gray-50 rounded-lg border border-gray-100">
            <h4 class="text-sm font-bold text-gray-700 mb-3">
              {{ isEditingCmd ? "编辑指令" : "新建指令" }}
            </h4>
            <div class="grid grid-cols-12 gap-4 mb-3">
              <div class="col-span-4">
                <input
                  v-model="editingCommand.label"
                  type="text"
                  placeholder="显示名称 (e.g. 🏢 列表)"
                  class="w-full text-xs border-gray-300 rounded focus:ring-primary focus:border-primary"
                />
              </div>
              <div class="col-span-6">
                <input
                  v-model="editingCommand.command"
                  type="text"
                  placeholder="执行内容"
                  class="w-full text-xs border-gray-300 rounded focus:ring-primary focus:border-primary"
                />
              </div>
              <div class="col-span-2">
                <input
                  v-model.number="editingCommand.sort_order"
                  type="number"
                  placeholder="排序"
                  class="w-full text-xs border-gray-300 rounded focus:ring-primary focus:border-primary"
                />
              </div>
            </div>
            <div class="flex justify-end space-x-2">
              <button
                v-if="isEditingCmd"
                @click="resetCommandForm"
                class="px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-300 rounded hover:bg-gray-50"
              >
                取消编辑
              </button>
              <button
                @click="saveCommand"
                class="px-3 py-1.5 text-xs text-white bg-primary rounded hover:bg-primary-dark transition-colors font-medium"
              >
                {{ isEditingCmd ? "保存修改" : "添加指令" }}
              </button>
            </div>
          </div>

          <!-- List -->
          <div class="space-y-2">
            <div
              v-for="(cmd, index) in slashCommands"
              :key="cmd.id"
              :draggable="currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name)"
              @dragstart="onDragStart($event, index)"
              @dragover.prevent
              @dragenter.prevent
              @drop="onDrop($event, index)"
              class="flex items-center justify-between p-3 border border-gray-100 rounded-lg hover:bg-gray-50 group transition-all duration-200"
              :class="{
                'opacity-50 scale-[0.98] bg-blue-50 border-blue-200': draggedItemIndex === index,
                'cursor-move': currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name),
                'cursor-default': !(currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name))
              }"
            >
              <div class="flex items-center space-x-3">
                <!-- Drag Handle (Only visible if draggable) -->
                <div v-if="currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name)" class="text-gray-300 group-hover:text-gray-400">
                  <svg
                    class="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M4 8h16M4 16h16"
                    />
                  </svg>
                </div>
                <div v-else class="w-4"></div> <!-- Spacer -->
                
                <span class="text-xs font-bold text-gray-500 w-6 text-center">{{
                  cmd.sort_order
                }}</span>
                <div class="flex items-center space-x-2">
                  <span
                    class="text-xs font-bold text-gray-800 bg-white border border-gray-200 px-2 py-0.5 rounded"
                    >{{ cmd.label }}</span
                  >
                  <!-- Badges -->
                  <span v-if="['system', 'admin'].includes(cmd.created_by)" class="px-1.5 py-0.5 rounded text-[10px] bg-purple-100 text-purple-700 border border-purple-200 font-medium">System</span>
                  <span v-else-if="currentUser && cmd.created_by === currentUser.user_name" class="px-1.5 py-0.5 rounded text-[10px] bg-blue-100 text-blue-700 border border-blue-200 font-medium">Mine</span>
                </div>
                
                <span class="text-xs text-gray-500 truncate max-w-xs">{{
                  cmd.command
                }}</span>
              </div>
              <div
                v-if="currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name)"
                class="flex items-center space-x-2 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <button
                  @click="editCommand(cmd)"
                  class="text-xs text-blue-600 hover:text-blue-800 underline"
                >
                  编辑
                </button>
                <button
                  @click="confirmDeleteCommand(cmd.id)"
                  class="text-xs text-red-600 hover:text-red-800 underline"
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Confirm Modal -->
  <ConfirmModal
    v-if="showDeleteConfirm"
    title="确认删除"
    message="确定要删除这个快捷指令吗？此操作无法撤销。"
    type="danger"
    confirm-text="删除"
    @confirm="performDeleteCommand"
    @cancel="showDeleteConfirm = false"
  />

  <!-- Modal: Logic Flow SVG -->
  <div
    v-if="showLogicFlowModal"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
  >
    <div
      class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl overflow-hidden animate-fade-in-up"
    >
      <div
        class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50/50"
      >
        <h3 class="font-bold text-gray-800 text-lg flex items-center">
          <svg
            class="w-5 h-5 text-blue-600 mr-2"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M13 10V3L4 14h7v7l9-11h-7z"
            />
          </svg>
          智能体运行逻辑架构 (Agent Execution Logic)
        </h3>
        <button
          @click="showLogicFlowModal = false"
          class="text-gray-400 hover:text-gray-600 p-2 rounded-full hover:bg-gray-100 transition-all"
        >
          <svg
            class="w-6 h-6"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>
      <div class="p-10 bg-white flex justify-center overflow-x-auto">
        <!-- SVG Architecture Diagram (Final Refined Version) -->
        <svg
          width="800"
          height="420"
          viewBox="0 0 800 420"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <!-- Definitions -->
          <defs>
            <marker
              id="arrowhead"
              markerWidth="10"
              markerHeight="7"
              refX="0"
              refY="3.5"
              orient="auto"
            >
              <polygon points="0 0, 10 3.5, 0 7" fill="#94a3b8" />
            </marker>
            <linearGradient id="blueGrad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#3b82f6" />
              <stop offset="100%" stop-color="#2563eb" />
            </linearGradient>
          </defs>

          <!-- Main Path: User -> Intent -> Agent Service -> Rendering -> Audit -->

          <!-- 1. User -->
          <rect
            x="20"
            y="160"
            width="110"
            height="50"
            rx="10"
            fill="#f8fafc"
            stroke="#e2e8f0"
            stroke-width="2"
          />
          <text
            x="75"
            y="185"
            text-anchor="middle"
            fill="#1e293b"
            font-weight="bold"
            font-size="13"
          >
            用户提问
          </text>
          <text
            x="75"
            y="200"
            text-anchor="middle"
            fill="#94a3b8"
            font-size="9"
          >
            User Query
          </text>
          <path
            d="M130 185 H160"
            stroke="#94a3b8"
            stroke-width="2"
            marker-end="url(#arrowhead)"
          />

          <!-- 2. Intent Recognition -->
          <rect
            x="170"
            y="160"
            width="130"
            height="50"
            rx="10"
            fill="#eff6ff"
            stroke="#3b82f6"
            stroke-width="2"
          />
          <text
            x="235"
            y="185"
            text-anchor="middle"
            fill="#1e3a8a"
            font-weight="bold"
            font-size="13"
          >
            意图识别
          </text>
          <text
            x="235"
            y="200"
            text-anchor="middle"
            fill="#3b82f6"
            font-size="9"
          >
            Intent Engine
          </text>
          <path
            d="M300 185 H350"
            stroke="#3b82f6"
            stroke-width="2"
            marker-end="url(#arrowhead)"
          />

          <!-- 3. Agent Service (Detailed ChatBI Path) -->
          <rect
            x="360"
            y="135"
            width="160"
            height="100"
            rx="12"
            fill="url(#blueGrad)"
            stroke="#1d4ed8"
            stroke-width="2"
            opacity="0.9"
          />
          <text
            x="440"
            y="175"
            text-anchor="middle"
            fill="white"
            font-weight="bold"
            font-size="15"
          >
            Agent Service
          </text>
          <text
            x="440"
            y="195"
            text-anchor="middle"
            fill="#bfdbfe"
            font-size="10"
          >
            ChatBI 核心编排
          </text>
          <text
            x="440"
            y="212"
            text-anchor="middle"
            fill="white"
            fill-opacity="0.6"
            font-size="9"
            font-family="monospace"
          >
            Tool-Calling
          </text>

          <!-- Metadata (Above) -->
          <path
            d="M440 135 V105"
            stroke="#94a3b8"
            stroke-width="1.5"
            stroke-dasharray="2 2"
            marker-end="url(#arrowhead)"
          />
          <rect
            x="380"
            y="50"
            width="120"
            height="45"
            rx="8"
            fill="#f0f9ff"
            stroke="#0ea5e9"
            stroke-width="1.5"
          />
          <text
            x="440"
            y="72"
            text-anchor="middle"
            fill="#0369a1"
            font-weight="bold"
            font-size="11"
          >
            Metadata Service
          </text>
          <text
            x="440"
            y="85"
            text-anchor="middle"
            fill="#0ea5e9"
            font-size="9"
          >
            元数据/Schema检索
          </text>

          <!-- External API (Below) -->
          <path
            d="M440 235 V265"
            stroke="#94a3b8"
            stroke-width="1.5"
            marker-end="url(#arrowhead)"
          />
          <rect
            x="370"
            y="275"
            width="140"
            height="50"
            rx="8"
            fill="#fff7ed"
            stroke="#f59e0b"
            stroke-width="1.5"
          />
          <text
            x="440"
            y="298"
            text-anchor="middle"
            fill="#9a3412"
            font-weight="bold"
            font-size="11"
          >
            外部数据接口 (8000)
          </text>
          <text
            x="440"
            y="312"
            text-anchor="middle"
            fill="#f59e0b"
            font-size="9"
          >
            SQL执行 / 获取结果
          </text>

          <!-- 4. Secondary Path: RAG & Chat (Dashed Branches Below) -->
          <path
            d="M235 210 V360 H360"
            stroke="#94a3b8"
            stroke-width="1.5"
            stroke-dasharray="4 3"
            marker-end="url(#arrowhead)"
          />
          <rect
            x="370"
            y="340"
            width="140"
            height="40"
            rx="8"
            fill="#f9fafb"
            stroke="#d1d5db"
            stroke-width="1.5"
            stroke-dasharray="4 2"
          />
          <text
            x="440"
            y="360"
            text-anchor="middle"
            fill="#64748b"
            font-weight="bold"
            font-size="11"
          >
            RAG / 基础闲聊
          </text>
          <text
            x="440"
            y="372"
            text-anchor="middle"
            fill="#9ca3af"
            font-size="8"
          >
            规划中能力 (Extension)
          </text>
          <path
            d="M510 360 H625 V245"
            stroke="#94a3b8"
            stroke-width="1.5"
            stroke-dasharray="4 3"
          />

          <!-- 5. Rendering -->
          <path
            d="M520 185 H570"
            stroke="#22c55e"
            stroke-width="2"
            marker-end="url(#arrowhead)"
          />
          <rect
            x="580"
            y="155"
            width="130"
            height="60"
            rx="12"
            fill="#f0fdf4"
            stroke="#22c55e"
            stroke-width="2"
          />
          <text
            x="645"
            y="185"
            text-anchor="middle"
            fill="#14532d"
            font-weight="bold"
            font-size="14"
          >
            结果合成渲染
          </text>
          <text
            x="645"
            y="200"
            text-anchor="middle"
            fill="#22c55e"
            font-size="10"
          >
            Markdown/ECharts
          </text>

          <!-- 6. Audit -->
          <path
            d="M710 185 H740"
            stroke="#94a3b8"
            stroke-width="2"
            marker-end="url(#arrowhead)"
          />
          <rect
            x="750"
            y="160"
            width="40"
            height="50"
            rx="8"
            fill="#fafafa"
            stroke="#d1d5db"
            stroke-width="1"
          />
          <text
            x="770"
            y="185"
            text-anchor="middle"
            fill="#4b5563"
            font-weight="bold"
            font-size="10"
          >
            审计
          </text>
          <text
            x="770"
            y="198"
            text-anchor="middle"
            fill="#9ca3af"
            font-size="8"
          >
            Trace
          </text>
        </svg>
      </div>
      <div
        class="px-8 py-6 bg-gray-50 border-t border-gray-100 flex items-start space-x-4"
      >
        <div class="bg-blue-100 p-2 rounded-lg">
          <svg
            class="w-5 h-5 text-blue-600"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        </div>
        <div class="text-xs text-gray-500 leading-relaxed">
          <p class="font-bold text-gray-700 mb-1">
            设计说明 (Architectural Notes):
          </p>
          1. <b>核心链路 (实线)</b>：专注于 ChatBI
          数据查询，支持元数据自动注入与外部 SQL (8000端口) 代理执行。<br />
          2. <b>扩展链路 (虚线)</b>：预留 RAG
          知识库与通用对话分支，统一由意图引擎分发，最终汇聚至渲染引擎。<br />
          3. <b>元数据与外部执行</b>：体现了 Agent
          在执行过程中的上下游依赖关系，确保查数逻辑的闭环。
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(156, 163, 175, 0.5);
}
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.3s ease;
}
.slide-fade-enter-from,
.slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
.slide-fade-left-enter-active,
.slide-fade-left-leave-active {
  transition: all 0.3s ease;
}
.slide-fade-left-enter-from,
.slide-fade-left-leave-to {
  transform: translateX(-20px);
  opacity: 0;
}
.animate-fade-in-up {
  animation: fadeInUp 0.3s ease-out;
}
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Markdown Styles */
:deep(.markdown-body) {
  font-size: 14px;
}
:deep(.markdown-body p) {
  margin-bottom: 1em;
}
:deep(.markdown-body p:last-child) {
  margin-bottom: 0;
}
:deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-weight: 600;
  margin-top: 1.5em;
  margin-bottom: 0.5em;
}
:deep(.markdown-body code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
    "Liberation Mono", "Courier New", monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 6px;
  font-size: 85%;
}
:deep(.markdown-body pre) {
  margin-top: 1em;
  margin-bottom: 1em;
  padding: 1.25em 1em 1em 1em;
  background-color: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: auto;
  position: relative;
  box-shadow: none;
}
:deep(.markdown-body pre):before {
  content: "";
  position: absolute;
  top: 10px;
  left: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #ff5f56;
  box-shadow: 16px 0 0 #ffbd2e, 32px 0 0 #27c93f;
  z-index: 1;
}
:deep(.markdown-body pre code) {
  background-color: transparent;
  padding: 0;
  font-size: 13px;
  font-family: "Fira Code", "JetBrains Mono", source-code-pro, Menlo, Monaco,
    Consolas, monospace;
  color: #24292e;
  line-height: 1.5;
}
/* Highlight.js Color Overrides for GitHub Light Style */
:deep(.hljs-keyword),
:deep(.hljs-selector-tag) {
  color: #d73a49;
}
:deep(.hljs-string) {
  color: #032f62;
}
:deep(.hljs-number) {
  color: #005cc5;
}
:deep(.hljs-type),
:deep(.hljs-built_in) {
  color: #6f42c1;
}
:deep(.hljs-attr),
:deep(.hljs-variable) {
  color: #e36209;
}
:deep(.hljs-comment) {
  color: #6a737d;
  font-style: italic;
}
:deep(.hljs-function) {
  color: #6f42c1;
}
:deep(.hljs-params) {
  color: #24292e;
}
:deep(.hljs-meta) {
  color: #005cc5;
}
:deep(.hljs-operator) {
  color: #d73a49;
}
:deep(.hljs-title) {
  color: #6f42c1;
}
:deep(.hljs-punctuation) {
  color: #24292e;
}
:deep(.markdown-body ul, .markdown-body ol) {
  padding-left: 2em;
  margin-bottom: 1em;
}
:deep(.markdown-body li) {
  margin-bottom: 0.25em;
}
:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 1em;
}
:deep(.markdown-body th, .markdown-body td) {
  border: 1px solid #d0d7de;
  padding: 6px 13px;
}
:deep(.markdown-body tr:nth-child(even)) {
  background-color: #f6f8fa;
}
:deep(.markdown-body .code-block-wrapper) {
  position: relative;
  margin-top: 1em;
  margin-bottom: 1em;
}
:deep(.markdown-body .code-block-wrapper pre) {
  margin: 0;
}
:deep(.markdown-body .code-copy-btn) {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3'/%3E%3C/svg%3E");
  background-size: 16px;
  background-repeat: no-repeat;
  background-position: center;
  background-color: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.2s;
  z-index: 10;
}
:deep(.markdown-body .code-block-wrapper:hover .code-copy-btn) {
  opacity: 1;
}
:deep(.markdown-body .code-copy-btn:hover) {
  background-color: #f3f4f6;
  border-color: #d1d5db;
}
:deep(.markdown-body .code-copy-btn.copied) {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2310b981'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M5 13l4 4L19 7'/%3E%3C/svg%3E");
  border-color: #10b981;
}
</style>
