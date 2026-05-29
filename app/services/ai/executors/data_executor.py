import time
import uuid
import json
import logging
import asyncio
import re
from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime

from langchain_core.messages import (
    HumanMessage, 
    AIMessage, 
    SystemMessage, 
    BaseMessage,
    ToolMessage
)
from app.schemas.agent import ChatConfig, AgentExecutionStep
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.config import AgentConfigProvider

from app.services.ai.executors.base import BaseExecutor

logger = logging.getLogger(__name__)

def _extract_tokens_from_message(msg: Any) -> dict:
    """
    Extract token usage from LangChain message or chunk.
    """
    res = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not msg:
        return res
    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
        um = msg.usage_metadata
        res["prompt_tokens"] = um.get("input_tokens") or 0
        res["completion_tokens"] = um.get("output_tokens") or 0
        res["total_tokens"] = um.get("total_tokens") or (res["prompt_tokens"] + res["completion_tokens"])
        return res
    if hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
        tu = msg.response_metadata.get("token_usage")
        if isinstance(tu, dict):
            res["prompt_tokens"] = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            res["completion_tokens"] = tu.get("completion_tokens") or tu.get("output_tokens") or 0
            res["total_tokens"] = tu.get("total_tokens") or (res["prompt_tokens"] + res["completion_tokens"])
            return res
    return res

class DataQueryExecutor(BaseExecutor):
    def __init__(self, config: ChatConfig, trace_id: str, trace_buffer: List[AgentExecutionStep], debug_options: Dict[str, Any] = None, user_info: Optional[Dict[str, Any]] = None, conversation_id: Optional[str] = None):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
        self.intent_info = None
        self._sql_plan_enforcement_added = False
        self._ratio_anomaly_feedback_sent = False
        self._schema_fetched_ok = False
        self._schema_no_authorized = False
        self._metadata_unavailable = False
        self._sql_attempted = False
        self._sql_succeeded = False
        self._sql_after_schema_nudge_sent = False
        self._current_user_question = ""
        self._no_tool_call_streak = 0

    def _increment_step(self):
        self.step_counter += 1

    def _has_sql_plan(self, text: str) -> bool:
        if not text:
            return False
        # Require an explicit tag to avoid accidental matches.
        return re.search(r"<sql_plan>\s*\{[\s\S]*?\}\s*</sql_plan>", text, flags=re.IGNORECASE) is not None

    def _should_require_sql_plan(self, user_question: str) -> bool:
        """
        Only enforce sql_plan for high-risk queries to avoid blocking simple list/detail queries.
        Heuristics: ratio/percent metrics, comparisons, trends, ranking, grouping/segmentation.
        """
        q = (user_question or "").strip().lower()
        if not q:
            return False
        # Chinese and English mixed keywords
        high_risk_keywords = [
            "率", "占比", "比例", "比率", "负载", "利用率", "pue", "成功率", "转化率", "人均", "单价",
            "同比", "环比", "趋势", "变化", "增长", "下降",
            "top", "排名", "排行", "分组", "按", "维度", "group", "join",
            "p95", "p90", "分位", "中位", "median", "percentile"
        ]
        return any(k in q for k in high_risk_keywords)

    def _try_parse_json_output(self, tool_output: Any) -> Any:
        """
        Best-effort parse tool output into structured JSON for preview/analysis.
        The external SQL API usually returns JSON string (list/dict).
        """
        if isinstance(tool_output, (list, dict)):
            return tool_output
        if not isinstance(tool_output, str):
            return tool_output
        s = tool_output.strip()
        if not s or s[0] not in "[{":
            return tool_output
        try:
            return json.loads(s)
        except Exception:
            return tool_output

    def _detect_ratio_anomaly(self, parsed: Any) -> Optional[str]:
        """
        Heuristic checks for 'partially correct' ratio outputs:
        - many rows with 0/100/extreme values
        - ratio columns outside expected range
        Returns a short reason string if anomaly is detected.
        """
        if not isinstance(parsed, list) or len(parsed) < 3:
            return None
        # Only handle list[dict] to keep this safe and generic.
        if not all(isinstance(r, dict) for r in parsed[: min(len(parsed), 10)]):
            return None

        # Find candidate ratio columns by name.
        sample_keys = set()
        for r in parsed[: min(len(parsed), 20)]:
            sample_keys.update(r.keys())
        ratio_cols = [k for k in sample_keys if re.search(r"(rate|ratio|pct|percent|percentage|load_rate|utilization)", str(k), re.IGNORECASE)]
        if not ratio_cols:
            return None

        def to_float(v):
            try:
                if v is None:
                    return None
                if isinstance(v, bool):
                    return float(v)
                if isinstance(v, (int, float)):
                    return float(v)
                sv = str(v).strip().replace("%", "")
                if sv == "":
                    return None
                return float(sv)
            except Exception:
                return None

        checks = []
        for col in ratio_cols[:3]:  # cap
            vals = []
            for r in parsed:
                fv = to_float(r.get(col))
                if fv is not None:
                    vals.append(fv)
            if len(vals) < 3:
                continue

            # If values look like 0-1, treat as ratio; else 0-100 as percent.
            v_min, v_max = min(vals), max(vals)
            is_ratio_01 = v_max <= 1.5
            # Count extremes
            if is_ratio_01:
                extreme = sum(1 for v in vals if v <= 0.001 or v >= 0.999)
                out_of_range = sum(1 for v in vals if v < -0.01 or v > 1.01)
            else:
                extreme = sum(1 for v in vals if v <= 0.1 or v >= 99.9)
                out_of_range = sum(1 for v in vals if v < -1 or v > 101)

            extreme_ratio = extreme / max(len(vals), 1)
            out_ratio = out_of_range / max(len(vals), 1)
            # Heuristic: many extremes OR any out-of-range indicates likely grain/join/denom issues.
            if out_ratio > 0:
                checks.append(f"列 {col} 存在超出合理区间的值")
            elif extreme_ratio >= 0.6 and (v_max - v_min) > (0.5 if is_ratio_01 else 50):
                checks.append(f"列 {col} 出现大量接近 0/100 的极端分布")

        if not checks:
            return None
        return "；".join(checks[:2])

    def _is_no_authorized_schema(self, tool_output: Any) -> bool:
        s = str(tool_output or "")
        return ("No authorized datasets found" in s) or ("未找到相关的授权数据集" in s)

    def _convert_history(self, history: List[Dict[str, str]]) -> List[BaseMessage]:
        messages = []
        for m in history:
            role = m["role"]
            content = m["content"]
            if role == "user":
                import base64
                import os
                files = m.get("files")
                img_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                img_files = []
                non_img_files = []
                if files:
                    for f in files:
                        if f.get("type") in ("skill", "knowledge_base"):
                            continue
                        ext = f.get("ext", "")
                        if not ext and f.get("url"):
                            ext = os.path.splitext(f["url"])[1]
                        if ext and ext.lower() in img_extensions:
                            img_files.append(f)
                        else:
                            non_img_files.append(f)
                
                # 构造非图片/Skills 的路径附随与引导提示词
                attachment_prompt = ""
                if non_img_files:
                    lines = ["\n\n【用户随附上传了非图片附件信息】："]
                    for f in non_img_files:
                        url = f.get("url", "")
                        filename = f.get("filename", "未知文件")
                        size_str = f"{(f.get('size', 0) / 1024):.1f} KB" if f.get("size") else "未知大小"
                        unique_name = os.path.basename(url)
                        abs_path = f"/app/data/uploads/{unique_name}"
                        lines.append(f"- 文件名: {filename} (大小: {size_str})")
                        lines.append(f"  服务器内绝对路径: {abs_path}")
                    lines.append("[系统提示: 以上非图片文件或 skills 配置已安全保存在服务器。如果您拥有相关的读取文件工具、数据分析工具、数据库工具或 Python 代码解释器工具，可以直接使用上述绝对路径读取文件内容并为用户进行深度分析。]")
                    attachment_prompt = "\n".join(lines)
                
                final_text = content + attachment_prompt
                
                if img_files:
                    multimodal_content = [{"type": "text", "text": final_text}]
                    for f in img_files:
                        url = f.get("url", "")
                        base64_data = None
                        if url.startswith("/static/uploads/"):
                            filename = os.path.basename(url)
                            local_path = os.path.join("data/uploads", filename)
                            if os.path.exists(local_path):
                                try:
                                    with open(local_path, "rb") as image_file:
                                        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
                                    ext_cleaned = f.get("ext", "png").lower().strip(".")
                                    if ext_cleaned == "jpg": ext_cleaned = "jpeg"
                                    mime_type = f"image/{ext_cleaned}"
                                    base64_data = f"data:{mime_type};base64,{encoded_string}"
                                except Exception as e:
                                    logger.warning(f"Failed to read local image for vision: {e}")
                        img_url = base64_data if base64_data else url
                        if img_url:
                            multimodal_content.append({
                                "type": "image_url",
                                "image_url": {"url": img_url}
                            })
                    messages.append(HumanMessage(content=multimodal_content))
                else:
                    messages.append(HumanMessage(content=final_text))
            elif role == "assistant": messages.append(AIMessage(content=content))
            elif role == "system": messages.append(SystemMessage(content=content))
        return messages

    def _current_user_id(self) -> Optional[int]:
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    def _is_last_result_followup(self, user_question: str) -> bool:
        q = (user_question or "").strip().lower()
        if not q:
            return False

        followup_keywords = [
            "可视化", "图表", "画图", "画个图", "柱状图", "折线图", "饼图", "分析一下", "总结一下",
            "解读一下", "基于上", "基于刚才", "根据上", "根据刚才", "上面的", "刚才的", "这个结果",
            "这些数据", "上一轮", "前面的", "按这个结果", "对这些",
            "visual", "chart", "plot", "graph", "analyze", "summarize",
        ]
        if not any(keyword in q for keyword in followup_keywords):
            return False

        new_query_keywords = [
            "重新查", "再查", "查询", "查一下", "查下", "统计", "列出", "筛选", "过滤", "最近",
            "今天", "昨天", "本周", "上周", "本月", "上月", "新增条件", "换成条件",
            "select ", "where ", "group by",
        ]
        return not any(keyword in q for keyword in new_query_keywords)

    async def _load_last_data_result_for_followup(self, user_question: str) -> Optional[Dict[str, Any]]:
        if not self.conversation_id or not self._is_last_result_followup(user_question):
            return None
        user_id = self._current_user_id()
        if not user_id:
            return None

        try:
            from app.services.ai.memory_service import memory_service
            return await memory_service.get_last_data_result(user_id, self.conversation_id)
        except Exception as e:
            logger.warning(f"[DataExecutor] Failed to load last data result: {e}")
            return None

    async def _save_last_data_result_for_followups(self, tool_call: Dict[str, Any], parsed_tool_output: Any) -> None:
        if not self.conversation_id or tool_call.get("name") != "execute_sql_query":
            return
        if not isinstance(parsed_tool_output, (list, dict)):
            return
        user_id = self._current_user_id()
        if not user_id:
            return

        args = tool_call.get("args") or {}
        payload = {
            "sql": args.get("sql") or args.get("query"),
            "data_source": args.get("data_source"),
            "dataset_name": args.get("dataset_name"),
            "rows": parsed_tool_output,
            "saved_at": datetime.now().isoformat(),
            "trace_id": self.trace_id,
        }
        try:
            from app.services.ai.memory_service import memory_service
            await memory_service.set_last_data_result(user_id, self.conversation_id, payload)
        except Exception as e:
            logger.warning(f"[DataExecutor] Failed to save last data result: {e}")

    async def _synthesize_from_last_data_result(
        self,
        langchain_messages: List[BaseMessage],
        system_prompt: str,
        user_question: str,
        last_result: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        start_synthesis = time.time()
        yield {
            "type": "log",
            "id": f"reuse_{uuid.uuid4().hex[:8]}",
            "title": "复用上一轮查询结果",
            "details": "检测到本轮是基于上一轮结果的分析/可视化请求，已跳过重新检索 Schema 与执行 SQL。",
            "status": "success",
        }
        yield {"type": "thinking", "status": "continuing"}

        prompt_without_menu = (system_prompt or "").replace("{dataset_menu}", "本轮复用上一轮结构化查询结果，不重新检索数据集。")
        result_json = json.dumps(last_result, ensure_ascii=False, indent=2)
        if len(result_json) > 30000:
            result_json = result_json[:30000] + "\n... [上一轮结果过长已截断]"

        synthesis_messages: List[BaseMessage] = [SystemMessage(content=prompt_without_menu)]
        for msg in langchain_messages[-6:-1]:
            if isinstance(msg, (HumanMessage, AIMessage)) and getattr(msg, "content", None):
                synthesis_messages.append(msg)
        synthesis_messages.append(HumanMessage(content=(
            f"【当前追问】：{user_question}\n\n"
            "【上一轮结构化查询结果】\n"
            f"{result_json}\n\n"
            "请只基于上一轮结构化查询结果完成分析或可视化，不要声称已重新查询数据库。\n"
            "如果适合可视化，请输出 markdown 结论并附带 ```chart JSON``` 图表配置。"
        )))

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        full_synthesis_content = ""
        content_emitted = False
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        accumulated_msg = None
        try:
            async for chunk in final_llm.astream(synthesis_messages):
                if accumulated_msg is None:
                    accumulated_msg = chunk
                else:
                    accumulated_msg += chunk
                if chunk.content:
                    if not content_emitted:
                        generation_start = time.time()
                        yield {"type": "log", "id": gen_log_id, "title": "✨ 开始生成回复", "status": "pending", "started_at": int(generation_start * 1000)}
                    content_emitted = True
                    full_synthesis_content += chunk.content
                    yield {"content": chunk.content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error(f"[DataExecutor] Follow-up synthesis failed: {syn_err}")
            fallback = "⚠️ 抱歉，基于上一轮结果生成分析时发生异常，请稍后重试。"
            full_synthesis_content = fallback
            yield {"type": "log", "id": f"syn_err_{uuid.uuid4().hex[:6]}", "title": "⚠️ 总结生成失败", "details": str(syn_err), "status": "error"}
            yield {"content": fallback}

        tokens = _extract_tokens_from_message(accumulated_msg)

        self._increment_step()
        self.trace_buffer.append(AgentExecutionStep(
            step_number=self.step_counter,
            event_type="synthesis",
            agent_name=self.config.agent_name,
            model=str(getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)),
            temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
            tool_output={"content": full_synthesis_content, "reused_last_data_result": True},
            raw_log=full_synthesis_content,
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            timestamp=datetime.fromtimestamp(start_synthesis),
        ))

    async def execute(self, history: List[Dict[str, str]]) -> AsyncGenerator[Dict[str, Any], None]:
        TOOL_LABEL_MAP = {"get_dataset_schema": "检索数据集定义", "execute_sql_query": "执行 SQL 查询", "update_dashboard_context": "更新看板关联状态"}
        import json
        import re

        # 1. Build Messages
        langchain_messages = self._convert_history(history)
        system_prompt = self.config.system_prompt
        self._current_user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "")

        if self._is_last_result_followup(self._current_user_question):
            last_result = await self._load_last_data_result_for_followup(self._current_user_question)
            if last_result:
                async for chunk in self._synthesize_from_last_data_result(
                    langchain_messages,
                    system_prompt,
                    self._current_user_question,
                    last_result,
                ):
                    yield chunk
            else:
                yield {
                    "type": "log",
                    "id": f"reuse_miss_{uuid.uuid4().hex[:8]}",
                    "title": "缺少可复用查询结果",
                    "details": "检测到本轮是基于上一轮结果的分析/可视化请求，但当前会话没有保存的结构化查询结果。",
                    "status": "error",
                }
                yield {"content": "当前会话没有可复用的上一轮查询结果，请先完成一次数据查询后再进行可视化或分析。"}
            return

        # 2. Prepare Tools

        prep_tools_start = time.time()
        prep_tools_log_id = f"prep_tools_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": prep_tools_log_id,
            "title": "准备工具",
            "details": "正在加载本轮可用工具...",
            "status": "pending",
            "started_at": int(prep_tools_start * 1000),
        }
        configured_tools = self.config.tools or ["get_dataset_schema", "execute_sql_query", "update_dashboard_context"]
        # Hard requirement: DataQueryExecutor must be able to execute SQL.
        if "execute_sql_query" not in configured_tools:
            configured_tools = list(configured_tools) + ["execute_sql_query"]
        tools = await ToolRegistry.get_tools(configured_tools)
        if not tools: tools = await ToolRegistry.get_tools(ToolRegistry.DEFAULT_TOOL_SET)
        tool_names = {getattr(t, "name", None) for t in tools}
        tool_names.discard(None)
        yield {
            "type": "log",
            "id": prep_tools_log_id,
            "title": "准备工具完成",
            "details": f"已加载工具: {', '.join(sorted(str(name) for name in tool_names)) if tool_names else '无'}",
            "status": "success",
            "execution_time_ms": (time.time() - prep_tools_start) * 1000,
        }

        # Reset per-execution state to avoid leaking across multiple execute() calls
        # (unit tests may reuse the same executor instance).
        self._ratio_anomaly_feedback_sent = False
        self._schema_fetched_ok = False
        self._schema_no_authorized = False
        self._metadata_unavailable = False
        self._sql_attempted = False
        self._sql_succeeded = False
        self._sql_after_schema_nudge_sent = False
        self._no_tool_call_streak = 0
        
        # [经验库] Few-Shot 检索与注入
        # 策略：将 Few-Shot 块插到 System Prompt【头部】，避免被 Lost-in-Middle 效应淹没
        # 同时保存 reminder，在 get_dataset_schema 返回后再次强调
        _few_shot_reminder = ""  # 用于 Schema 返回后的二次提醒
        _schema_reminder_injected = False  # 确保只注入一次
        fewshot_start = time.time()
        fewshot_log_id = f"fewshot_search_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": fewshot_log_id,
            "title": "检索经验库",
            "details": "正在查找可复用的历史优质 SQL 案例...",
            "status": "pending",
            "started_at": int(fewshot_start * 1000),
        }
        try:
            from app.services.chatbi_example_service import ExampleService
            user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "")
            if user_question:
                examples = await ExampleService.search_examples(
                    user_question, 
                    dataset_id=None, 
                    top_k=5,
                    history=langchain_messages
                )
                if examples:
                    max_sim = max([ex.get('similarity', 0) for ex in examples])
                    hit_titles = [f"#{ex.get('id', '?')} 「{ex['question'][:15]}...」 (相似度: {ex.get('similarity', 0):.2f})" for ex in examples]
                    sim_status = "匹配度极高" if max_sim >= 0.80 else "匹配度一般"
                    yield {
                        "type": "log", 
                        "id": f"fewshot_{uuid.uuid4().hex[:6]}", 
                        "title": f"✨ 命中经验库案例 ({len(examples)}条, {sim_status})", 
                        "details": f"已匹配到历史优质 SQL 案例：\n" + "\n".join(hit_titles) + f"\n\n当前最高相似度: {max_sim:.2f}。\n这些案例将作为强制性参考引导模型生成 SQL，以减少冗余迭代。",
                        "status": "success"
                    }
                    
                    few_shot_block = ExampleService.build_few_shot_prompt(examples)
                    if few_shot_block:
                        # [优化1] 插到 System Prompt 头部，确保模型在处理所有信息前先看到案例
                        system_prompt = few_shot_block + "\n\n---\n\n" + system_prompt
                        logger.info(f"[FewShot] Injected {len(examples)} examples at HEAD of system prompt for trace_id: {self.trace_id}")
                    
                    # [优化2] 构建二次提醒，用于 get_dataset_schema 返回后注入
                    _few_shot_reminder = ExampleService.build_few_shot_reminder(examples)
                    
                    # 异步记录使用统计
                    try:
                        example_ids = [ex["id"] for ex in examples if ex.get("id")]
                        similarities = [ex.get("similarity", 0) for ex in examples if ex.get("id")]
                        if example_ids:
                            asyncio.create_task(ExampleService.record_usage(example_ids, self.trace_id, similarities=similarities))
                    except Exception as ree:
                        logger.error(f"[FewShot] Failed to record usage statistic: {ree}")
        except Exception as fe:
            logger.warning(f"[FewShot] Failed to search/inject examples: {fe}")
        yield {
            "type": "log",
            "id": fewshot_log_id,
            "title": "检索经验库完成",
            "details": "经验库检索完成，继续构建本轮提示上下文。",
            "status": "success",
            "execution_time_ms": (time.time() - fewshot_start) * 1000,
        }

        if "{dataset_menu}" in system_prompt:
            # 注入用户权限信息
            user_id = self.user_info.get("user_id") if self.user_info else None
            is_admin = self.user_info.get("role") == "admin" if self.user_info else False
            
            dataset_menu_start = time.time()
            dataset_menu_log_id = f"dataset_menu_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": dataset_menu_log_id,
                "title": "加载数据集菜单",
                "details": "正在按当前用户权限加载可见数据集菜单...",
                "status": "pending",
                "started_at": int(dataset_menu_start * 1000),
            }
            dataset_menu = await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)
            system_prompt = system_prompt.replace("{dataset_menu}", dataset_menu)
            yield {
                "type": "log",
                "id": dataset_menu_log_id,
                "title": "加载数据集菜单完成",
                "details": "已完成数据集菜单注入。",
                "status": "success",
                "execution_time_ms": (time.time() - dataset_menu_start) * 1000,
            }
        langchain_messages.insert(0, SystemMessage(content=system_prompt))

        # --- [Accuracy Upgrade] Force Plan -> SQL workflow (generic) ---
        # We add a strict instruction once per executor instance to reduce grain/join errors.
        if not self._sql_plan_enforcement_added:
            langchain_messages.append(SystemMessage(content=(
                "【SQL 生成强约束（通用）】\n"
                "当你准备调用 execute_sql_query 之前，建议先在 <thought> 中输出一段结构化计划（用于提高准确性，避免 JOIN 放大/粒度错）。格式如下：\n"
                "<thought><sql_plan>{\n"
                "  \"dataset_name\": \"...\",\n"
                "  \"data_source\": \"...\",\n"
                "  \"grain_keys\": [\"...\"],\n"
                "  \"time_window\": {\"field\": \"...\", \"range\": \"...\"},\n"
                "  \"metrics_hit\": [\"...\"],\n"
                "  \"joins\": [{\"table\": \"...\", \"on\": \"...\", \"cardinality_risk\": \"1:1|1:N|N:M\"}],\n"
                "  \"ratio\": {\"numerator\": \"...\", \"denominator\": \"...\", \"denominator_semantics\": \"single_value|aggregate\"}\n"
                "}</sql_plan></thought>\n"
                "并遵循：先对齐粒度（CTE 聚合）→ 再 JOIN → 再计算比率/占比。禁止在明细粒度多对多 JOIN 后再聚合。\n"
            )))
            langchain_messages.append(SystemMessage(content=(
                "【追问复用约束】\n"
                "如果用户本轮只是要求“可视化一下 / 分析一下 / 总结一下 / 画个图 / 换成柱状图 / 基于刚才的数据”，"
                "且没有新增查询对象、筛选条件、时间范围或指标口径，应基于上一轮结构化查询结果分析，不要把“可视化/分析”当作 schema 检索关键词。\n"
                "只有用户明确提出重新查询、改变条件、改变时间范围或新增指标时，才进入新的 get_dataset_schema -> execute_sql_query 流程。\n"
            )))
            self._sql_plan_enforcement_added = True

        # 3. Model Setup
        # Use streaming for orchestration to allow potential turn-1 short-circuit
        model_setup_start = time.time()
        model_setup_log_id = f"model_setup_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": model_setup_log_id,
            "title": "准备模型",
            "details": "正在初始化模型并绑定工具...",
            "status": "pending",
            "started_at": int(model_setup_start * 1000),
        }
        model_with_tools = await AgentConfigProvider.get_configured_llm(streaming=True, config=self.config)
        model_with_tools = model_with_tools.bind_tools(tools)
        yield {
            "type": "log",
            "id": model_setup_log_id,
            "title": "准备模型完成",
            "details": "模型和工具绑定完成，准备进入首轮决策。",
            "status": "success",
            "execution_time_ms": (time.time() - model_setup_start) * 1000,
        }

        from app.services.config_service import ConfigService
        max_steps_str = await ConfigService.get("agent_max_iterations")
        # Keep a small cap to avoid long "no tool call" stalls.
        MAX_STEPS = int(max_steps_str) if max_steps_str else 6
        step = 0
        has_critical_error = False

        while step < MAX_STEPS:
            step += 1
            self._increment_step()
            yield {"type": "thinking", "status": "continuing"}
            
            start_thought = time.time()
            thought_log_id = f"thought_{self.step_counter}_{uuid.uuid4().hex[:6]}"
            yield {
                "type": "log",
                "id": thought_log_id,
                "title": f"模型决策: 第 {step} 轮",
                "details": "模型正在判断下一步动作和工具参数...",
                "status": "pending",
                "started_at": int(start_thought * 1000),
                "category": "tool",
            }
            accumulated_content = ""
            accumulated_msg = None
            has_tool_call_indicator = False

            try:
                async for chunk in model_with_tools.astream(langchain_messages):
                    if accumulated_msg is None: accumulated_msg = chunk
                    else: accumulated_msg += chunk
                    
                    if chunk.content:
                        if "<function_calls" in (accumulated_content + chunk.content):
                            has_tool_call_indicator = True
                        accumulated_content += chunk.content
            except Exception as stream_err:
                logger.error(f"[DataExecutor] Stream error during thought at step {step}: {stream_err}")
                yield {"type": "log", "id": f"err_{uuid.uuid4().hex[:6]}", "title": "⚠️ 模型响应异常", "details": f"流式输出中断: {str(stream_err)}", "status": "error"}
                break

            response = accumulated_msg
            tokens = _extract_tokens_from_message(response)
            if response is None:
                if step == 1 and not accumulated_content:
                    return # No response from model
                # Fallback to an empty message if we have content but no message object
                response = AIMessage(content=accumulated_content)

            if not response.tool_calls and "<function_calls>" in accumulated_content:
                response.tool_calls = self._parse_xml_tool_calls(accumulated_content)

            thought_elapsed_ms = (time.time() - start_thought) * 1000
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter, event_type="thought", agent_name=self.config.agent_name,
                model=str(self.config.model_name), temperature=float(self.config.temperature or 0),
                tool_output={"content": accumulated_content, "tool_calls": [tc['name'] for tc in response.tool_calls]},
                raw_log=accumulated_content, execution_time_ms=thought_elapsed_ms,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_thought)
            ))
            yield {
                "type": "log",
                "id": thought_log_id,
                "title": f"模型决策完成: 第 {step} 轮",
                "details": (
                    "本轮模型已完成内部决策。"
                    f"\n工具调用: {', '.join(tc['name'] for tc in response.tool_calls) if response.tool_calls else '无'}"
                ),
                "status": "success",
                "category": "tool",
                "execution_time_ms": thought_elapsed_ms,
            }

            if not response.tool_calls:
                # If SQL has already succeeded, don't prolong the loop on empty tool calls.
                # Break to final synthesis to reduce latency.
                if self._sql_succeeded:
                    langchain_messages.append(response)
                    break

                self._no_tool_call_streak += 1
                if self._no_tool_call_streak in (2, 4):
                    yield {
                        "type": "log",
                        "id": f"diag_{uuid.uuid4().hex[:8]}",
                        "title": "🧭 调用工具诊断",
                        "details": (
                            f"检测到模型连续 {self._no_tool_call_streak} 次未产生工具调用（tool_calls 为空）。\n"
                            "为确保数据准确性，系统将继续强制引导调用 get_dataset_schema / execute_sql_query。\n"
                            "若仍无法触发，请检查：模型是否支持工具调用、输出是否包含 <function_calls>、以及 agent 是否配置了 data_query 能力。"
                        ),
                        "status": "success",
                        "execution_time_ms": (time.time() - start_thought) * 1000,
                    }
                # Keep backward-compatible procrastination nudge (Turn 1, chatty, no tools)
                # Some unit tests and UI flows rely on this exact wording.
                if step == 1 and len(accumulated_content or "") > 50:
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=(
                        "检测到你在描述计划但未调用工具。请直接使用工具获取数据，不要只描述计划。"
                    )))
                    continue
                # If required tools are not available, don't spin in enforcement loops.
                # This happens in some unit tests (only schema tool is provided).
                if self._schema_fetched_ok and "execute_sql_query" not in tool_names:
                    langchain_messages.append(response)
                    break
                # Hard rule: DataQueryExecutor is not allowed to answer without querying data.
                # Always drive the model to tools (schema -> sql -> execute) across ALL steps.
                if not self._schema_fetched_ok:
                    must_msg = "你处于数据查询模式，禁止在未查数前给出回答。请先调用 get_dataset_schema(keywords) 获取 Schema。"
                elif not self._schema_no_authorized and not self._sql_attempted:
                    must_msg = "你已拿到 Schema，但尚未执行 SQL。禁止编造或直接总结。请立即调用 execute_sql_query(sql, data_source, dataset_name) 查数。"
                elif not self._schema_no_authorized and self._sql_attempted and not self._sql_succeeded:
                    must_msg = "你已尝试执行 SQL 但未成功。禁止直接回答。请根据错误信息修正 SQL 并再次调用 execute_sql_query。"
                else:
                    # Either no authorized datasets, or already executed successfully but model stopped calling tools.
                    must_msg = "你处于数据查询模式。若仍需数据支撑，请继续调用工具获取数据；否则仅在已执行查询成功且结果充分时再进入总结。"
                langchain_messages.append(response)
                langchain_messages.append(SystemMessage(content=must_msg))
                continue
            else:
                self._no_tool_call_streak = 0

            # --- [Plan Policy] Enforce plan only for high-risk queries; otherwise just nudge ---
            if any(tc.get("name") == "execute_sql_query" for tc in response.tool_calls) and not self._has_sql_plan(accumulated_content):
                require_plan = self._should_require_sql_plan(self._current_user_question)
                if require_plan:
                    # Block once and require plan in <thought> to keep UI clean.
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=(
                        "你当前问题属于高风险数据查询（包含比率/趋势/排名/分组等），为避免算错口径，执行 SQL 前必须先输出计划。\n"
                        "请先输出：<thought><sql_plan>{...}</sql_plan></thought>（简短即可，至少包含 dataset_name/data_source/grain_keys/time_window/joins/ratio）。\n"
                        "然后再调用 execute_sql_query。"
                    )))
                    continue
                else:
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=(
                        "提示：你将执行 SQL，但未输出 <thought><sql_plan>{...}</sql_plan></thought>。为提升准确性建议补齐（可简短）。本次不阻断执行。"
                    )))

            # Hard rule: after schema is fetched, you must execute SQL before doing anything else.
            if self._schema_fetched_ok and not self._schema_no_authorized and not self._sql_attempted:
                has_sql_call = any(tc.get("name") == "execute_sql_query" for tc in response.tool_calls)
                if not has_sql_call:
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=(
                        "你已经拿到数据集 Schema。下一步必须执行 SQL 查数（调用 execute_sql_query），禁止直接进入总结或输出结论。"
                    )))
                    continue

            # Process Tool Calls
            langchain_messages.append(response)
            parallel_tasks = []
            for tool_call in response.tool_calls:
                if not tool_call.get("id"): tool_call["id"] = f"call_{uuid.uuid4().hex[:8]}"
                tool_display = TOOL_LABEL_MAP.get(tool_call['name'], tool_call['name'])
                
                yield {
                    "type": "log",
                    "id": tool_call['id'],
                    "title": f"调用工具: {tool_display}",
                    "details": f"参数: {json.dumps(tool_call['args'], ensure_ascii=False)}",
                    "status": "pending",
                    "started_at": int(time.time() * 1000),
                }
                parallel_tasks.append(self._dispatch_tool_safe(tool_call, tools))
            # Parallel Execute
            execution_task = asyncio.ensure_future(asyncio.gather(*parallel_tasks))
            waited_seconds = 0
            while not execution_task.done():
                done, _ = await asyncio.wait([execution_task], timeout=1.5)
                if execution_task in done: break
                waited_seconds += 1.5
                for tc in response.tool_calls:
                     yield {
                         "type": "log",
                         "id": tc['id'],
                         "title": f"正在查询: {TOOL_LABEL_MAP.get(tc['name'], tc['name'])}",
                         "status": "pending",
                         "elapsed_time_ms": waited_seconds * 1000,
                     }

            results = execution_task.result()
            needs_followup_after_sql = False
            for tool_call, tool_output, duration_tool in results:
                # [Data Permission Tracing]
                # Merge trace logs into the tool's details instead of a separate card
                from app.core.context import get_current_agent_context
                ctx = get_current_agent_context()
                
                tool_display = TOOL_LABEL_MAP.get(tool_call['name'], tool_call['name'])

                # Try to parse tool output for better preview and downstream anomaly detection
                parsed_tool_output = self._try_parse_json_output(tool_output)
                final_details = str(tool_output)[:5000]

                # --- [修复] 先分析工具状态 ---
                tool_status, error_msg, is_sys_err = self._analyze_result(tool_output)
                if is_sys_err: 
                    has_critical_error = True

                # --- [优化] SQL 执行日志显示增强 ---
                # 强制所有 execute_sql_query 都显示SQL语句，不管状态如何
                if tool_call['name'] == "execute_sql_query":
                    self._sql_attempted = True
                    executed_sql = tool_call['args'].get('sql', '未知 SQL')
                    # 显示 SQL 和 结果摘要
                    results_preview = ""
                    if tool_status == "success":
                        self._sql_succeeded = True
                        await self._save_last_data_result_for_followups(tool_call, parsed_tool_output)
                        if isinstance(parsed_tool_output, list):
                            results_preview = f"\n\n✅ [查询成功]: 命中 {len(parsed_tool_output)} 行数据。"
                            if len(parsed_tool_output) > 0:
                                preview_rows = parsed_tool_output[:3]
                                results_preview += f"\n预览前{len(preview_rows)}行:\n" + json.dumps(preview_rows, ensure_ascii=False, indent=2)
                        elif isinstance(parsed_tool_output, dict):
                            results_preview = f"\n\n✅ [查询成功]: 返回对象结构。"
                            results_preview += "\n预览:\n" + json.dumps(parsed_tool_output, ensure_ascii=False, indent=2)[:800]
                        else:
                            results_preview = f"\n\n✅ [查询成功]: {str(tool_output)[:500]}"
                    elif tool_status == "error":
                        results_preview = f"\n\n❌ [执行失败]: {error_msg}"

                    final_details = f"📊 [执行 SQL]:\n```sql\n{executed_sql}\n```" + results_preview
                    logger.info(f"[DEBUG] SQL display with results: {executed_sql[:50]}...")

                    # --- [Accuracy Feedback] Detect ratio anomalies and request one-time self-correction ---
                    if tool_status == "success" and not self._ratio_anomaly_feedback_sent:
                        anomaly_reason = self._detect_ratio_anomaly(parsed_tool_output)
                        if anomaly_reason:
                            self._ratio_anomaly_feedback_sent = True
                            needs_followup_after_sql = True
                            langchain_messages.append(SystemMessage(content=(
                                f"【结果异常复核触发】检测到比率/占比类结果可能异常：{anomaly_reason}。\n"
                                "请不要直接给最终结论。你必须：\n"
                                "1) 复核统计粒度 grain_keys 与 JOIN 是否导致分子/分母被放大；\n"
                                "2) 如是比率/负载率/利用率等，请追加最多 1 条【对账 SQL】（必须同一过滤条件/同一时间窗），并强制返回以下字段别名：\n"
                                "   - grain_keys: 与当前输出一致的分组键（如 site_id/site_name/dept_id/day 等）\n"
                                "   - numerator_value: 分子（已按 grain 聚合后的数值）\n"
                                "   - denominator_value: 分母（已按 grain 对齐后的数值；若应为单值，用 MAX/MIN/ANY_VALUE 等确保不被 JOIN 放大）\n"
                                "   - ratio_calc: 复算比率 = numerator_value / NULLIF(denominator_value, 0)\n"
                                "   - ratio_returned: 原 SQL 返回的比率（同 grain_keys 对齐）\n"
                                "   - diff_pct: (ratio_returned - ratio_calc) / NULLIF(ratio_calc, 0)\n"
                                "   对账 SQL 输出行数应与当前分组行数一致（或可 join 对齐），并用于定位是 grain/单位/JOIN/分母异常导致。\n"
                                "   通用模板（示意，按实际字段/表名替换）：\n"
                                "   ```sql\n"
                                "   SELECT\n"
                                "     n.<grain_keys>,\n"
                                "     n.numerator_value,\n"
                                "     d.denominator_value,\n"
                                "     (n.numerator_value / NULLIF(d.denominator_value, 0)) AS ratio_calc,\n"
                                "     o.ratio_returned,\n"
                                "     ((o.ratio_returned - (n.numerator_value / NULLIF(d.denominator_value, 0))) / NULLIF((n.numerator_value / NULLIF(d.denominator_value, 0)), 0)) AS diff_pct\n"
                                "   FROM (\n"
                                "       SELECT <grain_keys>, <numerator_expr> AS numerator_value\n"
                                "       FROM <fact_tables>\n"
                                "       WHERE <same_filters>\n"
                                "       GROUP BY <grain_keys>\n"
                                "   ) n\n"
                                "   LEFT JOIN (\n"
                                "       SELECT <grain_keys>, <denominator_expr> AS denominator_value\n"
                                "       FROM <dim_or_fact_tables>\n"
                                "       WHERE <same_filters_or_dim_filters>\n"
                                "       GROUP BY <grain_keys>\n"
                                "   ) d USING (<grain_keys>)\n"
                                "   LEFT JOIN (\n"
                                "       -- 直接复用原 SQL 或抽取其结果为 ratio_returned\n"
                                "       SELECT <grain_keys>, <ratio_expr> AS ratio_returned\n"
                                "       FROM <original_query_or_cte>\n"
                                "   ) o USING (<grain_keys>)\n"
                                "   LIMIT 1000;\n"
                                "   ```\n"
                                "3) 若对账不一致，按“先聚合对齐粒度→再 JOIN→再计算”的 SELECT 子查询骨架重写原 SQL，再执行。\n"
                            )))

                # --- [新增] 针对元数据检索日志的精简与增强逻辑 ---
                if tool_call['name'] == "get_dataset_schema":
                    # 元数据服务不可用（如 RAGFlow 502）：禁止继续臆造 SQL，必须硬终止本次查询。
                    schema_out_str = tool_output if isinstance(tool_output, str) else json.dumps(tool_output, ensure_ascii=False)
                    if "[元数据服务不可用]" in schema_out_str:
                        self._metadata_unavailable = True
                        tool_status = "error"
                    if tool_status == "success" and not self._metadata_unavailable:
                        self._schema_fetched_ok = True
                        if self._is_no_authorized_schema(tool_output):
                            self._schema_no_authorized = True
                        # After schema is fetched, force an explicit, tool-call-only nudge once.
                        if (not self._schema_no_authorized) and (not self._sql_attempted) and (not self._sql_after_schema_nudge_sent):
                            self._sql_after_schema_nudge_sent = True
                            langchain_messages.append(SystemMessage(content=(
                                "【下一步强制动作】你已经拿到 Schema。现在禁止输出任何解释性文字，必须立刻调用 execute_sql_query 查数。\n"
                                "要求：\n"
                                "1) 先输出 <sql_plan>{...}</sql_plan>（简短即可），grain_keys 必须明确；\n"
                                "2) 随后直接发起 execute_sql_query 工具调用；\n"
                                "3) SQL 必须遵循 Grain-first：先聚合到 grain_keys，再 JOIN，再计算。\n"
                                "工具调用示例（参数名必须是 sql/data_source/dataset_name）：\n"
                                "<function_calls>\n"
                                "  <invoke name=\"execute_sql_query\">\n"
                                "    <parameter name=\"sql\">SELECT 1 LIMIT 1</parameter>\n"
                                "    <parameter name=\"data_source\">your_data_source</parameter>\n"
                                "    <parameter name=\"dataset_name\">your_dataset</parameter>\n"
                                "  </invoke>\n"
                                "</function_calls>"
                            )))
                    keywords = tool_call['args'].get('keywords', '未指定')
                    # 在日志详情顶部增加检索词
                    header = f"🔍 [检索关键词]: {keywords}\n" + "-"*30 + "\n"
                    
                    # 确保 [置信度: 0.XX] 的显示（如果 data_api 没加上，这里做兼容）
                    content = tool_output
                    if not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False)

                    if "[置信度:" not in content:
                        # 尝试从内容中匹配置信度并提升到头部
                        content = re.sub(r"IsRelevant:\s*([\d\.]+)", r"[置信度: \1]", content)
                    
                    # 精简显示：仅移除详情中的 columns 列表，防止吞掉后续数据集
                    # 改进正则：匹配 columns: 到下一个主要标记（置信度、Source、Dataset、Relationships或字符串结尾），
                    # 使用更严谨的边界判断，避免在 Local Mode 或大块 RAG 结果中过量匹配。
                    # 注意：YAML 中的 columns 通常是缩进的，这里暂用多行匹配并排除后续 Dataset/Source/Relationships 标记。
                    simplified_content = re.sub(r"columns:.*?(\nrelationships:|\n\[置信度:|\n--- Source:|\n--- Dataset:|\Z)", r"columns: [已隐藏具体字段定义，仅 AI 可见]\1", content, flags=re.DOTALL)

                    
                    # 如果精简后完全没变化且内容很长，说明可能没匹配到 columns 标记，直接使用 content
                    # [优化] 增加安全截断，防止极端情况下（如上百个数据集）发送超大负载
                    final_details = header + (simplified_content if simplified_content else content)
                    if len(final_details) > 15000:
                        final_details = final_details[:15000] + "\n\n... [内容过长已截断，请点击右侧详情图标查看完整 Trace]"
                    
                    # 兜底：如果 final_details 为空（不应该发生），确保至少有 header
                    if not final_details.strip():
                        final_details = header + "[检索完成，未发现匹配的数据集定义]"
                
                if tool_call['name'] == "execute_sql_query" and ctx and hasattr(ctx, "trace_logs") and ctx.trace_logs:
                    # Prepend rewrite info to details
                    trace_summary = "\n".join(ctx.trace_logs)
                    final_details = f"🔒 动态数据权限重写记录:\n{trace_summary}\n\n--- 原始查询结果 ---\n{final_details}"
                    tool_display = f"🔒 [权限重写] {tool_display}"
                    # Clear for next tool in same step if any
                    ctx.trace_logs = []
                
                target_inst = next((t for t in tools if t.name == tool_call["name"]), None)
                runtime_cfg = getattr(target_inst, "_runtime_config", None)
                t_model = getattr(runtime_cfg, "model_name", self.config.model_name)
                t_temp = getattr(runtime_cfg, "temperature", self.config.temperature)
                
                # Ensure types for AgentExecutionStep validation (especially when using Mocks in tests)
                if not isinstance(t_model, str): t_model = str(self.config.model_name)
                if not isinstance(t_temp, (int, float)): t_temp = float(self.config.temperature or 0)

                # Store structured tool output when possible (improves synthesis accuracy)
                tool_output_for_trace = parsed_tool_output
                self.trace_buffer.append(AgentExecutionStep(
                    step_number=self.step_counter, event_type="tool_call", agent_name=self.config.agent_name,
                    model=t_model, temperature=t_temp, tool_name=tool_call["name"], tool_input=tool_call["args"],
                    tool_output=tool_output_for_trace if isinstance(tool_output_for_trace, (dict, list)) else {"raw": tool_output},
                    execution_time_ms=duration_tool, status=tool_status, error_message=error_msg, timestamp=datetime.now()
                ))

                yield {
                    "type": "log",
                    "id": tool_call['id'],
                    "title": f"查询完成: {tool_display}",
                    "details": final_details,
                    "status": tool_status,
                    "execution_time_ms": duration_tool,
                }
                
                feedback = tool_output
                if tool_status == "error": feedback = self._get_self_healing_feedback(tool_call["name"], tool_output)
                if tool_call["name"] == "update_dashboard_context" and tool_status == "success":
                    yield {"type": "context_update", "data": tool_call["args"]}
                langchain_messages.append(ToolMessage(content=str(feedback), tool_call_id=tool_call["id"]))

                # 元数据服务不可用：硬终止，直接给出明确失败结论，绝不进入 execute_sql_query 臆造查询。
                if self._metadata_unavailable:
                    yield {
                        "type": "log",
                        "id": f"abort_{uuid.uuid4().hex[:8]}",
                        "title": "⛔ 终止：元数据服务不可用",
                        "details": "元数据检索服务（RAGFlow）不可用，已终止本次数据查询，避免编造表结构或 SQL。",
                        "status": "error",
                        "execution_time_ms": 0,
                    }
                    yield {"content": "⚠️ 元数据检索服务（RAGFlow）当前不可用，暂时无法获取数据集结构信息，因此无法完成本次数据查询。请稍后重试或联系管理员。"}
                    return

                # [优化2] get_dataset_schema 成功返回后，立即注入经验库案例二次提醒
                # 让模型在看完实时 Schema 数据后马上重新聚焦到参考案例的 SQL 逻辑
                if (tool_call["name"] == "get_dataset_schema" and tool_status == "success"
                        and _few_shot_reminder and not _schema_reminder_injected):
                    langchain_messages.append(SystemMessage(content=_few_shot_reminder))
                    _schema_reminder_injected = True
                    logger.info(f"[FewShot] Injected schema-reminder SystemMessage after get_dataset_schema")

            if has_critical_error:
                break

            if self._sql_succeeded and not needs_followup_after_sql:
                yield {
                    "type": "log",
                    "id": f"fast_path_{uuid.uuid4().hex[:8]}",
                    "title": "跳过额外决策",
                    "details": "SQL 已成功返回且未触发异常复核，直接进入汇总阶段。",
                    "status": "success",
                    "execution_time_ms": 0,
                }
                break

        # 5. Final Synthesis
        tool_msgs = [m for m in langchain_messages if isinstance(m, ToolMessage)]
        if not tool_msgs:
            # Should not happen in DataQueryExecutor, but keep safe.
            return

        # Hard gate: never synthesize a final answer without executing SQL (unless no authorized datasets).
        if not self._sql_attempted and not self._schema_no_authorized:
            yield {"type": "log", "id": f"gate_{uuid.uuid4().hex[:8]}", "title": "⚠️ 缺少 SQL 查询", "details": "未执行 execute_sql_query，已阻止生成回复以避免编造。", "status": "error"}
            yield {"content": "⚠️ 抱歉，本次未能成功执行数据查询，因此无法给出结论。请稍后重试或调整查询条件。"}
            return

        start_synthesis = time.time()
        syn_log_id = f"syn_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": syn_log_id,
            "title": "📝 汇总数据",
            "details": "正在分析查询结果...",
            "status": "pending",
            "started_at": int(start_synthesis * 1000),
        }
        yield {"type": "thinking", "status": "continuing"}

        # --- [STRATEGY B+: Context-Aware Synthesis] ---
        synthesis_messages = []
        # 1. System
        synthesis_messages.append(langchain_messages[0])
        # 2. Clean History (Skip current last human message)
        for msg in langchain_messages[1:-1]:
            if isinstance(msg, HumanMessage):
                synthesis_messages.append(msg)
            elif isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
                synthesis_messages.append(msg)
        
        # 3. Current Question + Data
        user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "无原始问题")
        
        # [IMPROVED] Use structured trace instead of just raw tool outputs
        execution_review = self._format_trace_for_synthesis(self.trace_buffer)
        
        synthesis_messages.append(HumanMessage(content=(
            f"【当前追问】：{user_question}\n\n"
            f"{execution_review}\n\n"
            "请结合上述【执行过程回顾】和查询结果，为用户提供连贯且专业的最终回答。\n"
            "注：如果执行过程主要是执行了一个外部动作（如发送消息、启动/暂停任务等），请直接简洁地告知执行结果即可，无需赘述。"
        )))

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        content_emitted = False
        full_synthesis_content = ""
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        accumulated_msg = None
        try:
            async for chunk in final_llm.astream(synthesis_messages):
                if accumulated_msg is None:
                    accumulated_msg = chunk
                else:
                    accumulated_msg += chunk
                if chunk.content:
                    if not content_emitted:
                         generation_start = time.time()
                         yield {
                             "type": "log",
                             "id": syn_log_id,
                             "title": "📝 汇总数据完成",
                             "details": "已完成查询结果分析，开始生成最终回复。",
                             "status": "success",
                             "execution_time_ms": (time.time() - start_synthesis) * 1000,
                         }
                         yield {"type": "log", "id": gen_log_id, "title": "✨ 开始生成回复", "status": "pending", "started_at": int(generation_start * 1000)}
                    content_emitted = True
                    full_synthesis_content += chunk.content
                    yield {"content": chunk.content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error(f"[DataExecutor] Stream error during synthesis: {syn_err}")
            yield {"type": "log", "id": f"syn_err_{uuid.uuid4().hex[:6]}", "title": "⚠️ 总结生成失败", "details": f"模型在总结阶段返回异常: {str(syn_err)}", "status": "error"}
            # 即使合成失败，也尝试将累积的内容保存或返回
            if not full_synthesis_content:
                full_synthesis_content = "⚠️ 抱歉，总结生成过程中发生模型异常，请参考上方的执行步骤。"
                yield {"content": full_synthesis_content}
        
        self._increment_step()
        s_model = getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)
        s_temp = self.config.synthesis_temperature or self.config.temperature
        
        if not isinstance(s_model, str): s_model = str(self.config.model_name)
        if not isinstance(s_temp, (int, float)): s_temp = float(self.config.temperature or 0.7)

        tokens = _extract_tokens_from_message(accumulated_msg)

        self.trace_buffer.append(AgentExecutionStep(
            step_number=self.step_counter, event_type="synthesis", agent_name=self.config.agent_name,
            model=s_model,
            temperature=s_temp,
            tool_output={"content": full_synthesis_content}, raw_log=full_synthesis_content,
            execution_time_ms=(time.time() - start_synthesis) * 1000, 
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            total_tokens=tokens["total_tokens"],
            timestamp=datetime.fromtimestamp(start_synthesis)
        ))

    def _format_trace_for_synthesis(self, traces: List[AgentExecutionStep]) -> str:
        """
        Formats the execution trace into a readable summary for the synthesis model.
        """
        lines = ["【执行过程回顾】"]
        
        # Filter only relevant steps (thoughts and tool calls) from the current session
        # We assume traces are ordered.
        
        current_step_num = -1
        
        for trace in traces:
            # Skip router or init steps if any
            if trace.event_type not in ["thought", "tool_call"]:
                continue
            
            if trace.step_number != current_step_num:
                lines.append(f"\nStep {trace.step_number}:")
                current_step_num = trace.step_number
            
            if trace.event_type == "thought":
                # Extract a summary of thought if possible, or just raw
                thought_content = trace.raw_log or ""
                # Try to clean up XML tags if present
                thought_content = re.sub(r'<function_calls>.*?</function_calls>', '', thought_content, flags=re.DOTALL)
                thought_content = thought_content.strip()
                if thought_content:
                    lines.append(f"  [思考] {thought_content}")
            
            elif trace.event_type == "tool_call":
                status_icon = "✅" if trace.status == "success" else "❌"
                output_str = str(trace.tool_output)
                # Truncate very long outputs for context window
                if len(output_str) > 2000:
                    output_str = output_str[:2000] + "... (truncated)"
                
                lines.append(f"  [操作] {trace.tool_name}({json.dumps(trace.tool_input, ensure_ascii=False)})")
                lines.append(f"  [结果] {status_icon} {output_str}")

        return "\n".join(lines)

    async def _dispatch_tool_safe(self, tool_call: Dict[str, Any], tools: List[Any]) -> tuple:
        start_tool = time.time()
        try:
            name = tool_call["name"]; args = tool_call["args"]
            # Keep tool input backward-compatible:
            # - old tools/tests expect {"query": "..."} for execute_sql_query
            # - newer prompts may use {"sql": "..."}
            invoke_args = args
            if isinstance(args, dict) and name == "execute_sql_query":
                invoke_args = dict(args)
                # Trim strings to reduce silly mismatches
                if isinstance(invoke_args.get("sql"), str):
                    invoke_args["sql"] = invoke_args["sql"].strip()
                if isinstance(invoke_args.get("query"), str):
                    invoke_args["query"] = invoke_args["query"].strip()
                # If only sql is provided, also provide query for legacy tools
                if "query" not in invoke_args and "sql" in invoke_args:
                    invoke_args["query"] = invoke_args.get("sql")
                # If only query is provided, do not inject sql (keeps unit tests strict)

            if self.debug_options.get("dry_run") and name == "execute_sql_query":
                output = f"[DRY RUN MODE] SQL skipped: {invoke_args.get('sql') or invoke_args.get('query')}"
            else:
                target_inst = next((t for t in tools if t.name == name), None)
                if not target_inst:
                    output = f"[TOOL_ERROR] Unknown tool: {name}"
                else:
                    from app.services.config_service import ConfigService

                    timeout_str = await ConfigService.get("agent_tool_timeout_seconds")
                    try:
                        timeout = float(timeout_str) if timeout_str else 60.0
                    except (TypeError, ValueError):
                        timeout = 60.0
                    output = await asyncio.wait_for(target_inst.ainvoke(invoke_args), timeout=timeout)
        except asyncio.TimeoutError:
            output = f"[TOOL_ERROR] Tool {tool_call.get('name')} timed out during execution."
        except Exception as e:
            output = f"[TOOL_ERROR] Execution failed: {str(e)}"
        return tool_call, output, (time.time() - start_tool) * 1000

    def _analyze_result(self, output: Any):
        """
        Heuristic tool status analyzer.

        NOTE: Avoid treating arbitrary JSON fields containing 'error' as a failure.
        Prefer explicit tool-error markers and common failure phrases.
        """
        if isinstance(output, (list, dict)):
            return "success", None, False

        out_str = str(output or "")
        low = out_str.lower()
        critical_signals = (
            "critical",
            "authentication failed",
            "unauthorized",
            "forbidden",
            "connection refused",
            "timed out",
            "timeout",
        )

        # Explicit executor-side tool error marker
        if low.startswith("[tool_error]"):
            return "error", out_str[:200], any(s in low for s in critical_signals)

        # Strong error signals
        strong_signals = (
            "traceback",
            "exception",
            "sql error",
            "syntax error",
            "permission denied",
            "access denied",
            "unauthorized",
            "forbidden",
            "timeout",
            "timed out",
            "connection refused",
            "failed",
        )
        if any(s in low for s in strong_signals):
            return "error", out_str[:200], any(s in low for s in critical_signals)

        # Best-effort: if it looks like JSON and can be parsed, treat as success.
        # (Many successful SQL APIs return JSON strings.)
        s = out_str.strip()
        if s[:1] in "[{":
            try:
                json.loads(s)
                return "success", None, False
            except Exception:
                pass

        return "success", None, False

    def _get_self_healing_feedback(self, tool_name: str, error_output: Any) -> str:
        err_str = str(error_output)
        if tool_name == "execute_sql_query":
            if "Unknown column" in err_str or "no such column" in err_str:
                return f"[规划修正] 字段名错误。工具 {tool_name} 报错: {err_str}。请先用 get_dataset_schema 确认表结构再重新生成 SQL。"
        return f"工具 {tool_name} 执行出错: {err_str}"

    def _parse_xml_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        import re; tool_calls = []
        match = re.search(r"<function_calls>(.*?)</function_calls>", content, re.DOTALL | re.IGNORECASE)
        if not match: return tool_calls
        try:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(match.group(0))
            for invoke in root.findall("invoke"):
                name = invoke.get("name")
                args = {p.get("name"): p.text for p in invoke.findall("parameter") if p.get("name")}
                if name: tool_calls.append({"name": name, "args": args, "id": f"xml_call_{uuid.uuid4().hex[:8]}"})
        except: pass
        return tool_calls
