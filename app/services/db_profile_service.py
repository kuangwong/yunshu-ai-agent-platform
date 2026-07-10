import logging
import json
import re
from typing import Any, Optional, Dict, List
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks

from app.core.orm import AsyncSessionLocal
from app.models.db_connection import MetaDbConnectionConfig, DbProfileTask, DbTableProfile
from app.services.db_connection_service import DbConnectionService
from app.services.db_import_service import DBImportService
from app.services.ai.config import AgentConfigProvider
from app.services.ai.runtime.agentscope.compat import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)


class DbProfileService:
    """外部数据源元数据智能摸排与分析服务"""

    @staticmethod
    async def trigger_profiling_task(
        db: AsyncSession,
        config_id: int,
        background_tasks: BackgroundTasks
    ) -> DbProfileTask:
        """
        触发该数据源配置下所有表和视图的智能分析摸排后台任务（一个数据源只允许一个进行中的任务）
        """
        # 1. 检查数据源配置是否存在
        config = await DbConnectionService.get_config(db, config_id)
        if not config:
            raise ValueError("数据源配置不存在")

        # 2. 检查是否有进行中的任务 (status = 1)
        stmt_task = select(DbProfileTask).where(DbProfileTask.connection_id == config_id)
        res_task = await db.execute(stmt_task)
        existing_task = res_task.scalar_one_or_none()

        if existing_task and existing_task.status == 1:
            raise ValueError("当前数据源分析摸排任务正在执行中，请勿重复点击")

        # 3. 实时查询该数据库下所有的表/视图
        db_config = {
            "host": config.host,
            "port": config.port,
            "user": config.db_user,
            "password": config.password,
            "database": config.database_name
        }

        db_type = config.db_type.strip().lower()
        if db_type == "mysql":
            tables_info = await DBImportService.get_mysql_tables(db_config)
        elif db_type == "clickhouse":
            tables_info = await DBImportService.get_clickhouse_tables(db_config)
        elif db_type == "oracle":
            tables_info = await DBImportService.get_oracle_tables(db_config)
        elif db_type in DBImportService._sqlserver_type_aliases():
            tables_info = await DBImportService.get_sqlserver_tables(db_config)
        else:
            raise ValueError(f"不支持的数据库类型: {config.db_type}")

        total_count = len(tables_info)

        # 4. Upsert 主任务状态
        if existing_task:
            existing_task.status = 1
            existing_task.total_tables = total_count
            existing_task.processed_tables = 0
            existing_task.current_table = None
            existing_task.error_message = None
            task = existing_task
        else:
            task = DbProfileTask(
                connection_id=config_id,
                status=1,
                total_tables=total_count,
                processed_tables=0,
                current_table=None
            )
            db.add(task)
        
        await db.flush()

        # 5. 准备并初始化子表记录 (db_table_profiles)
        # 获取现有的子表画像映射
        stmt_sub = select(DbTableProfile).where(DbTableProfile.connection_id == config_id)
        res_sub = await db.execute(stmt_sub)
        existing_profiles = {p.table_name: p for p in res_sub.scalars().all()}

        active_table_names = {t["name"] for t in tables_info}

        # 清除已物理不存在的表的草稿
        for t_name in list(existing_profiles.keys()):
            if t_name not in active_table_names:
                await db.delete(existing_profiles[t_name])
                del existing_profiles[t_name]

        # 初始化待处理状态
        for t in tables_info:
            t_name = t["name"]
            t_type = t.get("type", "table")
            if t_name in existing_profiles:
                p = existing_profiles[t_name]
                p.status = 0  # 待开始
                p.error_message = None
                p.table_type = t_type
            else:
                new_p = DbTableProfile(
                    connection_id=config_id,
                    table_name=t_name,
                    table_type=t_type,
                    status=0
                )
                db.add(new_p)

        await db.commit()

        # 6. 加入 FastAPI 后台任务
        background_tasks.add_task(DbProfileService.run_profiling_loop, config_id)
        return task

    @staticmethod
    async def get_task_status(db: AsyncSession, config_id: int) -> Optional[DbProfileTask]:
        """获取该数据源当前摸排任务进度与状态"""
        stmt = select(DbProfileTask).where(DbProfileTask.connection_id == config_id)
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def list_table_profiles(db: AsyncSession, config_id: int) -> List[DbTableProfile]:
        """获取该数据源下已摸排/分析的表画像列表"""
        stmt = select(DbTableProfile).where(DbTableProfile.connection_id == config_id).order_by(DbTableProfile.table_name)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def run_profiling_loop(config_id: int):
        """后台串行分析摸排主循环 (以单线程逐表异步执行)"""
        logger.info(f"[DbProfiling] Starting background profiling task for connection_id: {config_id}")
        try:
            # 1. 取得数据源配置与待分析表列表
            async with AsyncSessionLocal() as db:
                config = await DbConnectionService.get_config(db, config_id)
                if not config:
                    logger.error(f"[DbProfiling] DB Config {config_id} not found, exiting.")
                    return

                stmt = select(DbTableProfile).where(
                    DbTableProfile.connection_id == config_id,
                    DbTableProfile.status == 0
                ).order_by(DbTableProfile.table_name)
                res = await db.execute(stmt)
                pending_profiles = res.scalars().all()
                pending_tables = [{"table_name": p.table_name, "table_type": p.table_type} for p in pending_profiles]

            total_tables = len(pending_tables)
            logger.info(f"[DbProfiling] Connection {config_id} has {total_tables} tables to analyze.")

            # 2. 逐表处理
            for idx, table in enumerate(pending_tables):
                table_name = table["table_name"]
                logger.info(f"[DbProfiling] [{idx + 1}/{total_tables}] Profiling table: {table_name}")

                # 更新主任务状态为当前正在分析的表
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(DbProfileTask)
                        .where(DbProfileTask.connection_id == config_id)
                        .values(processed_tables=idx, current_table=table_name)
                    )
                    await db.execute(
                        update(DbTableProfile)
                        .where(DbTableProfile.connection_id == config_id, DbTableProfile.table_name == table_name)
                        .values(status=1)
                    )
                    await db.commit()

                # 执行 DDL 与 数据采样抓取
                try:
                    db_config = {
                        "host": config.host,
                        "port": config.port,
                        "user": config.db_user,
                        "password": config.password,
                        "database": config.database_name
                    }

                    # DDL 抓取
                    ddl = ""
                    db_type = config.db_type.strip().lower()
                    if db_type == "mysql":
                        ddl = await DBImportService.get_mysql_ddl(db_config, [table_name])
                    elif db_type == "clickhouse":
                        ddl = await DBImportService.get_clickhouse_ddl(db_config, [table_name])
                    elif db_type == "oracle":
                        ddl = await DBImportService.get_oracle_ddl(db_config, [table_name])
                    elif db_type in DBImportService._sqlserver_type_aliases():
                        ddl = await DBImportService.get_sqlserver_ddl(db_config, [table_name])

                    # 样例抓取 (SELECT LIMIT 3)
                    from app.services.data_adapter.factory import get_adapter
                    adapter = await get_adapter(config.name)

                    sample_data_json = "[]"
                    try:
                        # 兼容多引擎的采样查询构建
                        quote = "`" if db_type in ("mysql", "clickhouse") else '"'
                        if db_type == "oracle":
                            query_sql = f"SELECT * FROM {quote}{table_name}{quote} WHERE ROWNUM <= 3"
                        elif db_type in ("sqlserver", "mssql") or db_type in (getattr(DBImportService, "_sqlserver_type_aliases", lambda: [])()):
                            query_sql = f"SELECT TOP 3 * FROM {quote}{table_name}{quote}"
                        else:
                            query_sql = f"SELECT * FROM {quote}{table_name}{quote} LIMIT 3"
                        sample_res = await adapter.execute_sql(query_sql)

                        rows = sample_res.get("rows", [])
                        cols = sample_res.get("columns", [])

                        # 敏感长字段防溢出截断
                        sanitized_rows = []
                        for row in rows:
                            new_row = []
                            for val in row:
                                if isinstance(val, str) and len(val) > 150:
                                    new_row.append(val[:150] + "...")
                                else:
                                    new_row.append(val)
                            sanitized_rows.append(new_row)

                        sample_dicts = []
                        for row in sanitized_rows:
                            sample_dicts.append(dict(zip(cols, row)))

                        sample_data_json = json.dumps(sample_dicts, ensure_ascii=False)
                    except Exception as ex_sample:
                        logger.warning(f"[DbProfiling] Failed to fetch sample data for {table_name}: {ex_sample}")
                        sample_data_json = "[]"

                    # 3. 直接调用 LLM 进行推断
                    ai_res = await DbProfileService._analyze_table_with_llm(ddl, sample_data_json)

                    # 4. 成功后回填
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(DbTableProfile)
                            .where(DbTableProfile.connection_id == config_id, DbTableProfile.table_name == table_name)
                            .values(
                                ddl=ddl,
                                sample_data=sample_data_json,
                                ai_term=ai_res.get("ai_term"),
                                ai_description=ai_res.get("ai_description"),
                                ai_tags=ai_res.get("ai_tags"),
                                columns_profile=ai_res.get("columns"),
                                status=2,
                                error_message=None
                            )
                        )
                        await db.commit()

                except Exception as ex_item:
                    logger.exception(f"[DbProfiling] Table {table_name} profiling failed")
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(DbTableProfile)
                            .where(DbTableProfile.connection_id == config_id, DbTableProfile.table_name == table_name)
                            .values(status=3, error_message=str(ex_item))
                        )
                        await db.commit()

            # 3. 完成所有表后，将主任务置为成功 (2)
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(DbProfileTask)
                    .where(DbProfileTask.connection_id == config_id)
                    .values(status=2, processed_tables=total_tables, current_table=None)
                )
                await db.commit()
            logger.info(f"[DbProfiling] Finished background profiling task for connection_id: {config_id}")

        except Exception as total_ex:
            logger.exception(f"[DbProfiling] Fatal error in profiling task for config {config_id}")
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(DbProfileTask)
                    .where(DbProfileTask.connection_id == config_id)
                    .values(status=3, error_message=str(total_ex), current_table=None)
                )
                await db.commit()

    @staticmethod
    async def _analyze_table_with_llm(ddl: str, sample_data_json: str) -> Dict[str, Any]:
        """直接调用底层大模型解析元数据"""
        llm = await AgentConfigProvider.get_configured_llm(streaming=False)

        system_prompt = (
            "你是一个精通数据资产治理的数据库专家，擅长从建表语句和样例数据中提炼业务元数据含义。\n"
            "请根据提供的【建表 DDL】和【真实样例数据】，推测该表的中文业务术语（备注名）、表的一句话用途描述、表的分类标签，以及每个字段的中文术语和字段业务描述。\n\n"
            "【重要约束】\n"
            "1. 必须只返回一个 JSON 对象，不要 Markdown，不要多余解释。\n"
            "2. 返回的 JSON 必须符合以下 Schema 结构：\n"
            "{\n"
            '  "ai_term": "表的中文业务备注名，不超过100字，如: 机房能耗天报表",\n'
            '  "ai_description": "该表真实的业务用途与功能描述，不超过500字",\n'
            '  "ai_tags": ["标签1", "标签2"],\n'
            '  "columns": [\n'
            "    {\n"
            '      "name": "字段物理列名，如 room_id",\n'
            '      "term": "字段的中文业务术语/备注名，如 机房ID",\n'
            '      "desc": "该字段的业务解释描述"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        user_prompt = f"【建表 DDL】:\n{ddl}\n\n【样例数据】:\n{sample_data_json}"

        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        raw_text = getattr(response, "content", "") or str(response)
        return DbProfileService._extract_json(raw_text)

    @staticmethod
    def _extract_json(raw: str) -> Dict[str, Any]:
        """提取并解析返回的 JSON 内容"""
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except Exception:
            # 兼容 LLM 输出中可能存在的 markdown 标签包裹
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise ValueError(f"大模型返回内容无法解析为JSON: {raw}")
            return json.loads(match.group())
