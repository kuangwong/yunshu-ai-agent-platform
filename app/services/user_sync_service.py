import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user_sync import ThirdPartyUserSyncConfig, ThirdPartyUserSyncFieldMap
from app.services.auth_service import AuthService
from app.services.config_service import ConfigService
from app.services.data_adapter.factory import get_adapter
from app.services.db_connection_service import DbConnectionService

logger = logging.getLogger(__name__)

CONFIG_KEY = "third_party_user_sync_config"
SYNC_REMARK_PREFIX = "第三方同步"


class UserSyncService:
    @staticmethod
    def _default_config() -> ThirdPartyUserSyncConfig:
        return ThirdPartyUserSyncConfig()

    @staticmethod
    async def get_config() -> ThirdPartyUserSyncConfig:
        raw = await ConfigService.get(CONFIG_KEY)
        if not raw:
            return UserSyncService._default_config()
        try:
            data = json.loads(raw)
            return ThirdPartyUserSyncConfig(**data)
        except Exception as exc:
            logger.warning("Invalid third_party_user_sync_config: %s", exc)
            return UserSyncService._default_config()

    @staticmethod
    async def save_config(
        config: ThirdPartyUserSyncConfig,
        changed_by: str = "admin",
    ) -> ThirdPartyUserSyncConfig:
        payload = config.model_dump()
        await ConfigService.set_config(
            CONFIG_KEY,
            json.dumps(payload, ensure_ascii=False),
            description="第三方用户同步配置（数据源、表、字段映射、定时周期）",
            category="other",
            is_secret=False,
            changed_by=changed_by,
            change_reason="更新第三方用户同步配置",
        )
        from app.services.ai.scheduler_service import scheduler_service

        await scheduler_service.reschedule_third_party_user_sync(config)
        return config

    @staticmethod
    def schedule_to_cron(schedule: str) -> Optional[Dict[str, Any]]:
        if schedule == "hourly":
            return {"minute": 0}
        if schedule == "daily":
            return {"hour": 2, "minute": 0}
        if schedule == "weekly":
            return {"day_of_week": "mon", "hour": 2, "minute": 0}
        return None

    @staticmethod
    def _quote_ident(name: str, db_type: str) -> str:
        safe = (name or "").strip()
        if not safe or not re.match(r"^[A-Za-z0-9_$.]+$", safe):
            raise ValueError(f"非法标识符: {name}")
        db = (db_type or "mysql").lower()
        if db in ("sqlserver", "mssql"):
            return f"[{safe.replace(']', ']]')}]"
        if db == "oracle":
            return f'"{safe.upper()}"'
        return f"`{safe.replace('`', '``')}`"

    @staticmethod
    def _build_select_sql(
        table_name: str,
        field_map: ThirdPartyUserSyncFieldMap,
        db_type: str,
    ) -> str:
        if not table_name:
            raise ValueError("未配置目标表")
        if not field_map.id or not field_map.user_name:
            raise ValueError("用户 ID 和用户名字段映射为必填项")

        selects: List[str] = []
        mapping = {
            "id": field_map.id,
            "user_name": field_map.user_name,
            "real_name": field_map.real_name,
            "remark": field_map.remark,
        }
        for alias, source_col in mapping.items():
            if source_col:
                selects.append(
                    f"{UserSyncService._quote_ident(source_col, db_type)} AS {alias}"
                )

        table_sql = UserSyncService._quote_ident(table_name, db_type)
        return f"SELECT {', '.join(selects)} FROM {table_sql}"

    @staticmethod
    async def _get_adapter_for_config(
        db: AsyncSession,
        connection_config_id: int,
    ):
        config = await DbConnectionService.get_config(db, connection_config_id)
        if not config:
            raise ValueError("数据源不存在")
        return config, await get_adapter(config.name)

    @staticmethod
    async def list_tables(db: AsyncSession, connection_config_id: int) -> List[Dict[str, str]]:
        db_config, adapter = await UserSyncService._get_adapter_for_config(
            db, connection_config_id
        )
        if not hasattr(adapter, "get_tables"):
            raise ValueError(f"数据源类型 {db_config.db_type} 暂不支持表列表查询")
        return await adapter.get_tables()

    @staticmethod
    async def list_columns(
        db: AsyncSession,
        connection_config_id: int,
        table_name: str,
    ) -> List[Dict[str, str]]:
        _, adapter = await UserSyncService._get_adapter_for_config(db, connection_config_id)
        if not hasattr(adapter, "get_columns"):
            raise ValueError("当前数据源适配器不支持列查询")
        return await adapter.get_columns(table_name=table_name)

    @staticmethod
    def _rows_to_dicts(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        col_names = [c.get("name") for c in result.get("columns", [])]
        items: List[Dict[str, Any]] = []
        for row in result.get("rows", []):
            if isinstance(row, dict):
                items.append(row)
            else:
                items.append(dict(zip(col_names, row)))
        return items

    @staticmethod
    def _normalize_external_user(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raw_id = row.get("id")
        user_name = row.get("user_name")
        if raw_id is None or user_name is None:
            return None
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        username = str(user_name).strip()
        if not username:
            return None
        return {
            "id": user_id,
            "user_name": username,
            "real_name": (str(row.get("real_name")).strip() if row.get("real_name") is not None else None),
            "remark": (str(row.get("remark")).strip() if row.get("remark") is not None else None),
        }

    @staticmethod
    async def fetch_external_users(
        db: AsyncSession,
        config: ThirdPartyUserSyncConfig,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        if not config.connection_config_id or not config.table_name:
            raise ValueError("请先配置数据源和目标表")

        db_config, adapter = await UserSyncService._get_adapter_for_config(
            db, config.connection_config_id
        )
        sql = UserSyncService._build_select_sql(
            config.table_name, config.field_map, db_config.db_type
        )
        result = await adapter.preview(sql, limit=limit)
        users: List[Dict[str, Any]] = []
        for row in UserSyncService._rows_to_dicts(result):
            normalized = UserSyncService._normalize_external_user(row)
            if normalized:
                users.append(normalized)
        return users

    @staticmethod
    async def preview_users(
        db: AsyncSession,
        config: Optional[ThirdPartyUserSyncConfig] = None,
    ) -> List[Dict[str, Any]]:
        cfg = config or await UserSyncService.get_config()
        if not cfg.enabled and config is None:
            pass
        external_users = await UserSyncService.fetch_external_users(db, cfg)

        ids = [u["id"] for u in external_users]
        existing_ids: set[int] = set()
        if ids:
            stmt = select(User.id).where(User.id.in_(ids))
            existing_ids = set((await db.execute(stmt)).scalars().all())

        items = []
        for user in external_users:
            items.append(
                {
                    **user,
                    "is_synced": user["id"] in existing_ids,
                }
            )
        return items

    @staticmethod
    async def run_sync(
        db: AsyncSession,
        user_ids: Optional[List[int]] = None,
        config: Optional[ThirdPartyUserSyncConfig] = None,
    ) -> Dict[str, int]:
        cfg = config or await UserSyncService.get_config()
        if not cfg.connection_config_id or not cfg.table_name:
            raise ValueError("同步配置不完整，请先保存数据源与字段映射")
        if not cfg.field_map.id or not cfg.field_map.user_name:
            raise ValueError("用户 ID 和用户名字段映射为必填项")

        external_users = await UserSyncService.fetch_external_users(db, cfg)
        if user_ids:
            selected = set(user_ids)
            external_users = [u for u in external_users if u["id"] in selected]

        skipped = 0
        created = 0
        failed = 0

        for ext_user in external_users:
            user_id = ext_user["id"]
            existing = await db.get(User, user_id)
            if existing:
                skipped += 1
                continue

            username_check = await db.execute(
                select(User.id).where(User.user_name == ext_user["user_name"])
            )
            if username_check.scalar_one_or_none() is not None:
                logger.warning(
                    "Skip third-party user id=%s: username %s already exists",
                    user_id,
                    ext_user["user_name"],
                )
                skipped += 1
                continue

            remark = ext_user.get("remark") or ""
            if remark and not remark.startswith(SYNC_REMARK_PREFIX):
                remark = f"{SYNC_REMARK_PREFIX}: {remark}"
            elif not remark:
                remark = SYNC_REMARK_PREFIX

            try:
                await AuthService.generate_api_key(
                    user_name=ext_user["user_name"],
                    real_name=ext_user.get("real_name"),
                    role="user",
                    remark=remark,
                    user_id=user_id,
                    db=db,
                )
                created += 1
            except Exception as exc:
                logger.error("Failed to sync third-party user %s: %s", user_id, exc)
                failed += 1

        return {"created": created, "skipped": skipped, "failed": failed}
