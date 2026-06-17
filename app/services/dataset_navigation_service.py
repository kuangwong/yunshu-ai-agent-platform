"""基于 ChatBI {dataset_menu} 与用户权限，由 LLM 生成我的数据门户（含 quick 追问按钮）。"""
from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)

_NAV_CACHE_TTL_SECONDS = 600
_NAV_PROMPT_VERSION = "v3"
_CLICK_STATS_TTL_SECONDS = 90 * 24 * 60 * 60


def _user_cache_key(*, user_id: Optional[int], is_admin: bool) -> str:
    if is_admin:
        return "admin"
    if user_id is not None:
        return str(user_id)
    return "anon"


def count_datasets_in_menu(dataset_menu: str) -> int:
    return len(re.findall(r"^- Dataset:", str(dataset_menu or ""), flags=re.MULTILINE))


def menu_has_authorized_datasets(dataset_menu: str) -> bool:
    text = str(dataset_menu or "")
    if not text.strip():
        return False
    if "No authorized datasets" in text:
        return False
    return count_datasets_in_menu(text) > 0


def _question_hash(query: str) -> str:
    return hashlib.sha1(str(query or "").strip().encode("utf-8")).hexdigest()[:16]


def _decode_redis_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


class DatasetNavigationService:
    @staticmethod
    async def _get_dataset_menu(
        *,
        user_id: Optional[int],
        is_admin: bool,
    ) -> str:
        return await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)

    @staticmethod
    async def _load_cached_navigation(cache_key: str) -> Optional[str]:
        try:
            redis = await get_redis()
            if redis:
                cached = await redis.get(cache_key)
                if cached:
                    return str(cached)
        except Exception as e:
            logger.warning("Dataset navigation cache read failed: %s", e)
        return None

    @staticmethod
    async def _save_cached_navigation(cache_key: str, markdown: str) -> None:
        try:
            redis = await get_redis()
            if redis:
                await redis.set(cache_key, markdown, ex=_NAV_CACHE_TTL_SECONDS)
        except Exception as e:
            logger.warning("Dataset navigation cache write failed: %s", e)

    @staticmethod
    async def _generate_navigation_markdown(dataset_menu: str) -> str:
        if not menu_has_authorized_datasets(dataset_menu):
            return DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)

        fallback = DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)
        try:
            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            chat_client = chat_client_from_handle(llm)
            content = await chat_client.generate_text(
                [
                    RuntimeMessage(
                        role="system",
                        content=[
                            RuntimeContentBlock(
                                type="text",
                                text=DataQueryPrompts.dataset_navigation_generation_prompt(dataset_menu),
                            )
                        ],
                    )
                ]
            )
            cleaned = str(content or "").strip()
            if cleaned and DataQueryPrompts.has_quick_suggestions(cleaned):
                return finalize_visible_reply(cleaned, collapse_duplicates=False)
        except Exception as e:
            logger.warning("Dataset navigation LLM generation failed: %s", e)
        return finalize_visible_reply(fallback, collapse_duplicates=False)

    @staticmethod
    def _click_rank_key(*, user_key: str, dataset_menu_hash: str) -> str:
        return f"agent:dataset_navigation_click_rank:{user_key}:{dataset_menu_hash}"

    @staticmethod
    def _click_meta_key(*, user_key: str, dataset_menu_hash: str) -> str:
        return f"agent:dataset_navigation_click_meta:{user_key}:{dataset_menu_hash}"

    @staticmethod
    async def _load_question_click_stats(
        *,
        user_key: str,
        dataset_menu_hash: str,
    ) -> Dict[str, Dict[str, Any]]:
        try:
            redis = await get_redis()
            if not redis:
                return {}
            rank_key = DatasetNavigationService._click_rank_key(
                user_key=user_key,
                dataset_menu_hash=dataset_menu_hash,
            )
            meta_key = DatasetNavigationService._click_meta_key(
                user_key=user_key,
                dataset_menu_hash=dataset_menu_hash,
            )
            ranked = await redis.zrevrange(rank_key, 0, -1, withscores=True)
            raw_meta = await redis.hgetall(meta_key)
            meta_map: Dict[str, Any] = {}
            if isinstance(raw_meta, dict):
                meta_map = {
                    str(_decode_redis_value(k)): _decode_redis_value(v)
                    for k, v in raw_meta.items()
                }

            stats: Dict[str, Dict[str, Any]] = {}
            for item in ranked or []:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    member, score = item[0], item[1]
                else:
                    member, score = item, 0
                question_id = str(_decode_redis_value(member))
                meta_raw = meta_map.get(question_id)
                if not meta_raw:
                    continue
                try:
                    meta = json.loads(str(meta_raw))
                except Exception:
                    continue
                query = str(meta.get("query") or "").strip()
                if not query:
                    continue
                stats[query] = {
                    "count": int(score or 0),
                    "last_clicked_at": meta.get("last_clicked_at"),
                    "label": meta.get("label"),
                    "group_id": meta.get("group_id"),
                }
            return stats
        except Exception as e:
            logger.warning("Dataset navigation click stats read failed: %s", e)
            return {}

    @staticmethod
    def _apply_question_click_stats(
        groups: list[dict[str, Any]],
        click_stats: Dict[str, Dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not click_stats:
            return groups

        ranked_groups: list[dict[str, Any]] = []
        for group in groups:
            copied = dict(group)
            questions = []
            for index, question in enumerate(group.get("questions") or []):
                query = str(question.get("query") or "")
                stats = click_stats.get(query) or {}
                enriched = dict(question)
                enriched["click_count"] = int(stats.get("count") or 0)
                if stats.get("last_clicked_at"):
                    enriched["last_clicked_at"] = stats["last_clicked_at"]
                enriched["_original_order"] = index
                questions.append(enriched)

            questions.sort(
                key=lambda q: (
                    int(q.get("click_count") or 0),
                    str(q.get("last_clicked_at") or ""),
                    -int(q.get("_original_order") or 0),
                ),
                reverse=True,
            )
            for question in questions:
                question.pop("_original_order", None)
            copied["questions"] = questions
            ranked_groups.append(copied)
        return ranked_groups

    @staticmethod
    async def record_question_click(
        *,
        user_id: Optional[int],
        is_admin: bool,
        dataset_menu_hash: str,
        query: str,
        label: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> None:
        clean_hash = str(dataset_menu_hash or "").strip()
        clean_query = str(query or "").strip()
        if not clean_hash or not clean_query:
            return
        try:
            redis = await get_redis()
            if not redis:
                return
            user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
            question_id = _question_hash(clean_query)
            rank_key = DatasetNavigationService._click_rank_key(
                user_key=user_key,
                dataset_menu_hash=clean_hash,
            )
            meta_key = DatasetNavigationService._click_meta_key(
                user_key=user_key,
                dataset_menu_hash=clean_hash,
            )
            now = datetime.now(timezone.utc).isoformat()
            metadata = {
                "query": clean_query,
                "label": str(label or "").strip(),
                "group_id": str(group_id or "").strip(),
                "last_clicked_at": now,
            }
            await redis.zincrby(rank_key, 1, question_id)
            await redis.hset(meta_key, question_id, json.dumps(metadata, ensure_ascii=False))
            await redis.expire(rank_key, _CLICK_STATS_TTL_SECONDS)
            await redis.expire(meta_key, _CLICK_STATS_TTL_SECONDS)
        except Exception as e:
            logger.warning("Dataset navigation click stats write failed: %s", e)

    @staticmethod
    def parse_groups_from_markdown(
        markdown: str,
        static_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """从 LLM 生成的 markdown 文本中，动态解析出各个场景下的推荐问题和继续追问，
        并与静态分析的 static_groups 进行合并。
        """
        if not markdown or not static_groups:
            return static_groups

        import copy
        groups = copy.deepcopy(static_groups)

        parts = markdown.split("#### ")
        if len(parts) <= 1:
            return groups

        def clean_str(s: str) -> str:
            return re.sub(r"[^\w]", "", s).lower()

        pattern = r"-\s*\[(?:[^\]]*🙋\s*)?([^\]]+)\]\(quick:([^\)]+)\)"

        for part in parts[1:]:
            lines = part.splitlines()
            if not lines:
                continue
            title = lines[0].strip()
            if not title:
                continue

            matched_group = None
            clean_title = clean_str(title)
            for g in groups:
                clean_g_title = clean_str(g.get("title") or "")
                if clean_title == clean_g_title or clean_title in clean_g_title or clean_g_title in clean_title:
                    matched_group = g
                    break

            if not matched_group:
                continue

            lower_part = part.lower()
            split_term = "**继续追问：**".lower()
            split_idx = lower_part.find(split_term)

            if split_idx != -1:
                questions_part = part[:split_idx]
                followups_part = part[split_idx:]
            else:
                questions_part = part
                followups_part = ""

            dynamic_questions = []
            for match in re.finditer(pattern, questions_part):
                label = match.group(1).strip()
                query = match.group(2).strip()
                if "/dataset_menu" in query or "重新查看" in label:
                    continue
                dynamic_questions.append({
                    "label": label,
                    "query": query,
                    "type": "dynamic",
                })

            dynamic_followups = []
            for match in re.finditer(pattern, followups_part):
                label = match.group(1).strip()
                query = match.group(2).strip()
                if "/dataset_menu" in query or "重新查看" in label:
                    continue
                dynamic_followups.append({
                    "label": label,
                    "query": query,
                })

            summary_lines = []
            for line in lines[1:]:
                stripped = line.strip()
                if stripped.startswith(">"):
                    summary_lines.append(stripped.lstrip(">").strip())

            if dynamic_questions:
                matched_group["questions"] = dynamic_questions
            if dynamic_followups:
                matched_group["followups"] = dynamic_followups
            if summary_lines:
                matched_group["summary"] = "\n".join(summary_lines)

        return groups

    @staticmethod
    async def build_navigation_for_user(
        db: AsyncSession,
        *,
        user_id: Optional[int],
        is_admin: bool,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        del db  # 与 ChatBI 一致，数据集目录来自 AgentConfigProvider.get_dataset_menu
        dataset_menu = await DatasetNavigationService._get_dataset_menu(
            user_id=user_id,
            is_admin=is_admin,
        )
        dataset_count = count_datasets_in_menu(dataset_menu)

        menu_hash = hashlib.md5(dataset_menu.encode("utf-8")).hexdigest()[:12]
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        groups = DataQueryPrompts.build_dataset_navigation_groups(dataset_menu)
        cache_key = f"agent:dataset_navigation:{user_key}:{menu_hash}:{_NAV_PROMPT_VERSION}"

        markdown = None if force_refresh else await DatasetNavigationService._load_cached_navigation(cache_key)
        if not markdown:
            markdown = await DatasetNavigationService._generate_navigation_markdown(dataset_menu)
            await DatasetNavigationService._save_cached_navigation(cache_key, markdown)

        # 动态解析 markdown 中的场景和推荐问题
        groups = DatasetNavigationService.parse_groups_from_markdown(markdown, groups)

        # 应用用户点击的偏好排序
        click_stats = await DatasetNavigationService._load_question_click_stats(
            user_key=user_key,
            dataset_menu_hash=menu_hash,
        )
        groups = DatasetNavigationService._apply_question_click_stats(groups, click_stats)

        return {
            "dataset_count": dataset_count,
            "dataset_menu_hash": menu_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "groups": groups,
            "markdown": markdown,
        }
