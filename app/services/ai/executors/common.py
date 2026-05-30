"""Executor 公共工具：历史转换、Token 提取、XML 工具解析。"""
from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.services.ai.executors.prompts import SharedPrompts

logger = logging.getLogger(__name__)


def extract_tokens_from_message(msg: Any) -> dict:
    """从 LangChain message/chunk 提取 token 用量。"""
    res = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not msg:
        return res
    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
        um = msg.usage_metadata
        res["prompt_tokens"] = um.get("input_tokens") or 0
        res["completion_tokens"] = um.get("output_tokens") or 0
        res["total_tokens"] = um.get("total_tokens") or (
            res["prompt_tokens"] + res["completion_tokens"]
        )
        return res
    if hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
        tu = msg.response_metadata.get("token_usage")
        if isinstance(tu, dict):
            res["prompt_tokens"] = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            res["completion_tokens"] = tu.get("completion_tokens") or tu.get("output_tokens") or 0
            res["total_tokens"] = tu.get("total_tokens") or (
                res["prompt_tokens"] + res["completion_tokens"]
            )
            return res
    return res


def convert_history_to_messages(history: List[Dict[str, str]]) -> List[BaseMessage]:
    """将平台 messages 转为 LangChain BaseMessage 列表（含附件/多模态）。"""
    messages: List[BaseMessage] = []
    for m in history:
        role = m["role"]
        content = m["content"]
        if role == "user":
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

            attachment_prompt = ""
            if non_img_files:
                lines = [SharedPrompts.NON_IMAGE_ATTACHMENT_HEADER]
                for f in non_img_files:
                    url = f.get("url", "")
                    filename = f.get("filename", "未知文件")
                    size_str = (
                        f"{(f.get('size', 0) / 1024):.1f} KB" if f.get("size") else "未知大小"
                    )
                    unique_name = os.path.basename(url)
                    abs_path = f"/app/data/uploads/{unique_name}"
                    lines.append(f"- 文件名: {filename} (大小: {size_str})")
                    lines.append(f"  服务器内绝对路径: {abs_path}")
                lines.append(SharedPrompts.NON_IMAGE_ATTACHMENT_FOOTER)
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
                                if ext_cleaned == "jpg":
                                    ext_cleaned = "jpeg"
                                mime_type = f"image/{ext_cleaned}"
                                base64_data = f"data:{mime_type};base64,{encoded_string}"
                            except Exception as e:
                                logger.warning("Failed to read local image for vision: %s", e)
                    img_url = base64_data if base64_data else url
                    if img_url:
                        multimodal_content.append(
                            {"type": "image_url", "image_url": {"url": img_url}}
                        )
                messages.append(HumanMessage(content=multimodal_content))
            else:
                messages.append(HumanMessage(content=final_text))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "system":
            messages.append(SystemMessage(content=content))
    return messages


def parse_xml_tool_calls(content: str) -> List[Dict[str, Any]]:
    """解析模型输出的 <function_calls> XML 工具调用块。"""
    tool_calls: List[Dict[str, Any]] = []
    match = re.search(r"<function_calls>(.*?)</function_calls>", content, re.DOTALL | re.IGNORECASE)
    if not match:
        match = re.search(r"<function_calls>(.*)", content, re.DOTALL | re.IGNORECASE)
    if not match:
        return tool_calls
    xml_content = match.group(0)
    try:
        from xml.etree import ElementTree as ET

        fixed_xml = xml_content.replace("</invokefunction_calls>", "</invoke></function_calls>")
        if not fixed_xml.endswith("</function_calls>"):
            fixed_xml += "</function_calls>"
        root = ET.fromstring(fixed_xml)
        for invoke in root.findall("invoke"):
            name = invoke.get("name")
            args = {}
            for param in invoke.findall("parameter"):
                p_name = param.get("name")
                p_value = param.text
                if p_name:
                    args[p_name] = p_value
            if name:
                tool_calls.append({"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"})
    except Exception:
        pass
    return tool_calls


def tools_include_named(tools: List[Any], tool_name: str) -> bool:
    for t in tools or []:
        if getattr(t, "name", None) == tool_name:
            return True
    return False
