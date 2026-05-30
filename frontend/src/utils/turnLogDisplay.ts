/** 与后端 TurnType 对齐，用于深度思考步骤折叠展示 */

export type TurnType =
  | "k1_new_query"
  | "k2_reuse_result"
  | "k3_context_action"
  | "skill_execution"
  | "meta_action"
  | "general"
  | "knowledge";

export interface TurnLogLike {
  title?: string;
  category?: string;
  status?: string;
}

const TURN_PANEL_TITLES: Record<TurnType, string> = {
  k1_new_query: "K1 · 新查数",
  k2_reuse_result: "K2 · 复用结果",
  k3_context_action: "K3 · 上下文动作",
  skill_execution: "技能执行",
  meta_action: "元操作",
  general: "通用对话",
  knowledge: "知识库问答",
};

/** ChatBI 数据流水线步骤（非 K1 时可折叠隐藏） */
const DATA_PIPELINE_KEYWORDS = [
  "经验库",
  "schema",
  "数据集",
  "sql",
  "检索数据集",
  "准备工具",
  "加载数据集菜单",
];

const ALWAYS_VISIBLE_KEYWORDS = [
  "轮次分类",
  "多智能体",
  "结果聚合",
  "路由",
  "技能",
  "知识",
  "检索",
  "引用",
  "生成回复",
  "准备回答",
  "复用",
  "合成",
  "强制",
];

function titleLower(log: TurnLogLike): string {
  return (log.title || "").toLowerCase();
}

function matchesAny(text: string, keywords: string[]): boolean {
  const lower = text.toLowerCase();
  return keywords.some((k) => lower.includes(k.toLowerCase()));
}

function isDataPipelineStep(log: TurnLogLike): boolean {
  const t = titleLower(log);
  if (log.category === "sql") return true;
  return matchesAny(t, DATA_PIPELINE_KEYWORDS);
}

function isAlwaysVisible(log: TurnLogLike): boolean {
  if (log.category === "intent" || log.category === "router") return true;
  return matchesAny(log.title || "", ALWAYS_VISIBLE_KEYWORDS);
}

/** 按 Turn 类型过滤应展示的 log（原始 logs 仍全量保留） */
export function filterLogsForTurn<T extends TurnLogLike>(
  logs: T[] | undefined,
  turnType?: TurnType | string | null,
): T[] {
  if (!logs?.length) return [];
  if (!turnType) return logs;

  switch (turnType as TurnType) {
    case "k2_reuse_result":
      return logs.filter(
        (log) =>
          isAlwaysVisible(log) ||
          log.category === "tool" ||
          log.category === "knowledge" ||
          !isDataPipelineStep(log),
      );
    case "k3_context_action":
    case "meta_action":
      return logs.filter(
        (log) => isAlwaysVisible(log) || log.category === "tool" || !isDataPipelineStep(log),
      );
    case "knowledge":
      return logs.filter(
        (log) =>
          isAlwaysVisible(log) ||
          log.category === "knowledge" ||
          log.category === "tool" ||
          !isDataPipelineStep(log),
      );
    case "skill_execution":
      return logs.filter(
        (log) => isAlwaysVisible(log) || log.category === "tool" || !matchesAny(titleLower(log), ["经验库"]),
      );
    case "k1_new_query":
    default:
      return logs;
  }
}

export function getTurnPanelTitle(
  turnType?: TurnType | string | null,
  isThinking?: boolean,
): string {
  if (isThinking) return "思考中...";
  if (turnType && turnType in TURN_PANEL_TITLES) {
    return TURN_PANEL_TITLES[turnType as TurnType];
  }
  return "深度思考过程";
}

export function defaultThoughtExpanded(turnType?: TurnType | string | null): boolean {
  return turnType === "k1_new_query";
}

export function countHiddenLogs(
  allLogs: TurnLogLike[] | undefined,
  visibleLogs: TurnLogLike[],
): number {
  return Math.max(0, (allLogs?.length || 0) - visibleLogs.length);
}
