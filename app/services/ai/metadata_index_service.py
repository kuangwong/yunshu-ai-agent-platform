"""RediSearch-backed metadata index service for local vector search."""
import logging
import struct
from typing import Any, Dict, List, Optional
from app.core.redis import get_redis, get_redis_binary
from app.services.ai.embedding_client import EmbeddingClient

logger = logging.getLogger(__name__)

METADATA_INDEX_NAME = "yunshu:idx:metadata:dataset"
METADATA_KEY_PREFIX = "metadata:dataset:"

def _vector_to_bytes(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def _tag_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(".", "\\.")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(";", "\\;")
        .replace("!", "\\!")
        .replace("@", "\\@")
        .replace("#", "\\#")
        .replace("$", "\\$")
        .replace("%", "\\%")
        .replace("^", "\\^")
        .replace("&", "\\&")
        .replace("*", "\\*")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("-", "\\-")
        .replace("+", "\\+")
        .replace("=", "\\=")
        .replace("~", "\\~")
        .replace("|", "\\|")
        .replace(" ", "\\ ")
    )

class MetadataIndexService:
    @staticmethod
    async def index_name() -> str:
        return METADATA_INDEX_NAME

    @staticmethod
    def _parse_ft_info(info: Any) -> Dict[str, Any]:
        if not info or not isinstance(info, (list, tuple)):
            return {}
        parsed: Dict[str, Any] = {}
        i = 0
        while i + 1 < len(info):
            key = info[i]
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="replace")
            parsed[str(key)] = info[i + 1]
            i += 2
        return parsed

    @staticmethod
    def _extract_index_vector_dim(info: Dict[str, Any]) -> Optional[int]:
        attrs = info.get("attributes")
        if not isinstance(attrs, (list, tuple)):
            return None
        stack: List[Any] = [attrs]
        while stack:
            node = stack.pop()
            if not isinstance(node, (list, tuple)):
                continue
            for idx in range(len(node) - 1):
                key = node[idx]
                if isinstance(key, bytes):
                    key = key.decode("utf-8", errors="replace")
                if key == "dim":
                    try:
                        return int(node[idx + 1])
                    except (TypeError, ValueError):
                        return None
            for item in node:
                if isinstance(item, (list, tuple)):
                    stack.append(item)
        return None

    @staticmethod
    def _append_trace(trace: Optional[List[str]], message: str) -> None:
        if trace is not None:
            trace.append(message)

    @staticmethod
    async def append_index_diagnostics(trace: Optional[List[str]] = None) -> Dict[str, Any]:
        """Collect Redis / RediSearch index state for schema retrieval troubleshooting."""
        from app.core.config import settings

        diag: Dict[str, Any] = {
            "redis_enabled": settings.REDIS_ENABLE,
            "redis_host": settings.REDIS_HOST,
            "redis_port": settings.REDIS_PORT,
            "redis_db": settings.REDIS_DB,
        }
        MetadataIndexService._append_trace(
            trace,
            f"[Redis] REDIS_ENABLE={settings.REDIS_ENABLE}, "
            f"target={settings.REDIS_HOST}:{settings.REDIS_PORT}/db{settings.REDIS_DB}",
        )

        configured_dim = await EmbeddingClient.get_dimensions(use_global=True)
        diag["configured_embed_dim"] = configured_dim
        MetadataIndexService._append_trace(trace, f"[Embedding Config] embed_dimensions={configured_dim}")

        redis = await get_redis()
        if not redis:
            diag["text_client"] = False
            MetadataIndexService._append_trace(trace, "[Redis] Text client unavailable")
            return diag
        diag["text_client"] = True

        binary = await get_redis_binary()
        diag["binary_client"] = binary is not None
        if not binary:
            MetadataIndexService._append_trace(
                trace,
                "[WARN] Redis binary client unavailable — vector KNN will return 0 without raising",
            )

        idx = await MetadataIndexService.index_name()
        try:
            info = MetadataIndexService._parse_ft_info(await redis.execute_command("FT.INFO", idx))
            num_docs = info.get("num_docs", "?")
            index_dim = MetadataIndexService._extract_index_vector_dim(info)
            diag["index_exists"] = True
            diag["num_docs"] = num_docs
            diag["index_vector_dim"] = index_dim
            MetadataIndexService._append_trace(
                trace,
                f"[RediSearch] Index '{idx}' num_docs={num_docs}, index_vector_dim={index_dim}",
            )
            if index_dim is not None and index_dim != configured_dim:
                MetadataIndexService._append_trace(
                    trace,
                    f"[WARN] Index vector dim ({index_dim}) != embed_dimensions ({configured_dim}) "
                    "— run 「重构本地向量数据」 after fixing config",
                )
        except Exception as exc:
            diag["index_exists"] = False
            MetadataIndexService._append_trace(
                trace,
                f"[RediSearch] FT.INFO '{idx}' failed: {exc}",
            )

        key_count = 0
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{METADATA_KEY_PREFIX}*", count=200)
            key_count += len(keys or [])
            if cursor == 0:
                break
        diag["hash_key_count"] = key_count
        MetadataIndexService._append_trace(
            trace,
            f"[Redis Keys] SCAN '{METADATA_KEY_PREFIX}*' => {key_count} hash key(s)",
        )

        num_docs_val = diag.get("num_docs")
        try:
            num_docs_int = int(num_docs_val) if num_docs_val is not None else -1
        except (TypeError, ValueError):
            num_docs_int = -1
        if key_count > 0 and num_docs_int == 0:
            MetadataIndexService._append_trace(
                trace,
                "[WARN] Hash keys exist but index num_docs=0 — index out of sync; rebuild vectors",
            )
        elif num_docs_int > 0 and key_count == 0:
            MetadataIndexService._append_trace(
                trace,
                "[WARN] Index has documents but no metadata hash keys under prefix",
            )

        return diag

    @staticmethod
    async def ensure_index() -> bool:
        redis = await get_redis()
        if not redis:
            return False
        idx = await MetadataIndexService.index_name()
        dim = await EmbeddingClient.get_dimensions(use_global=True)
        try:
            info = await redis.execute_command("FT.INFO", idx)
            if info:
                return True
        except Exception:
            pass
        try:
            await redis.execute_command(
                "FT.CREATE",
                idx,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                METADATA_KEY_PREFIX,
                "SCHEMA",
                "dataset_id",
                "TAG",
                "doc_name",
                "TEXT",
                "content",
                "TEXT",
                "embedding",
                "VECTOR",
                "HNSW",
                "6",
                "TYPE",
                "FLOAT32",
                "DIM",
                str(dim),
                "DISTANCE_METRIC",
                "COSINE",
            )
            logger.info("[MetadataIndex] Created index %s dim=%s", idx, dim)
            return True
        except Exception as e:
            logger.warning("[MetadataIndex] FT.CREATE failed: %s", e)
            return False

    @staticmethod
    async def upsert_vector(
        dataset_id: int,
        doc_name: str,
        content: str,
        embedding: List[float]
    ) -> None:
        """
        Upsert a metadata document (table or metrics) with embedding into Redis HASH.
        Key is:
          - for table: metadata:dataset:<dataset_id>:table:<table_physical_name>
          - for metrics: metadata:dataset:<dataset_id>:metrics
        """
        redis = await get_redis()
        if not redis:
            return
        
        if doc_name == "_metrics.txt":
            key = f"{METADATA_KEY_PREFIX}{dataset_id}:metrics"
        else:
            table_name = doc_name
            if table_name.endswith(".txt"):
                table_name = table_name[:-4]
            key = f"{METADATA_KEY_PREFIX}{dataset_id}:table:{table_name}"

        mapping = {
            "dataset_id": str(dataset_id),
            "doc_name": doc_name,
            "content": content,
            "embedding": _vector_to_bytes(embedding)
        }
        await redis.hset(key, mapping=mapping)
        logger.info("[MetadataIndex] Upserted vector for key %s", key)
        await MetadataIndexService.ensure_index()

    @staticmethod
    async def delete_dataset_vectors(dataset_id: int) -> None:
        """
        Cascade delete all keys under prefix metadata:dataset:<dataset_id>:
        """
        redis = await get_redis()
        if not redis:
            return
        
        prefix = f"{METADATA_KEY_PREFIX}{dataset_id}:"
        cursor = 0
        keys_to_delete = []
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{prefix}*", count=100)
            if keys:
                keys_to_delete.extend(keys)
            if cursor == 0:
                break
        
        if keys_to_delete:
            await redis.delete(*keys_to_delete)
            logger.info("[MetadataIndex] Deleted %d keys for dataset_id %d", len(keys_to_delete), dataset_id)

    @staticmethod
    async def sync_local_redis_vector(dataset_id: int) -> None:
        """
        Synchronize a dataset's metadata to local Redis vectors asynchronously.
        """
        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService
        from app.services.ai.embedding_client import EmbeddingClient
        from app.services.metadata_rag_service import MetadataRagService
        import asyncio

        async def _run_sync():
            try:
                async with AsyncSessionLocal() as db:
                    # 1. Load Dataset Detail (Admin mode)
                    dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
                    if not dataset:
                        logger.error(f"[Local Redis Sync] Dataset {dataset_id} not found.")
                        return

                    logger.info(f"[Local Redis Sync] Starting sync for dataset: {dataset.name} (ID: {dataset_id})")

                    # Check prefix/index existence first
                    await MetadataIndexService.ensure_index()

                    expected_docs = {}
                    relationships = dataset.relationships or []
                    
                    for table in dataset.tables:
                        if hasattr(table, "status") and table.status != 1:
                            continue
                        file_name = f"{table.physical_name}.txt"
                        content = MetadataRagService.generate_table_content(dataset, table, relationships)
                        expected_docs[file_name] = content

                    if dataset.metrics:
                        metrics_content = MetadataRagService.generate_metrics_content(dataset)
                        if metrics_content:
                            expected_docs["_metrics.txt"] = metrics_content

                    # 2. Get existing keys from Redis for this dataset
                    redis = await get_redis()
                    if not redis:
                        logger.warning("[Local Redis Sync] Redis not available, skipping.")
                        return
                    
                    prefix = f"metadata:dataset:{dataset_id}:"
                    cursor = 0
                    existing_keys = []
                    while True:
                        cursor, keys = await redis.scan(cursor, match=f"{prefix}*", count=100)
                        if keys:
                            existing_keys.extend(keys)
                        if cursor == 0:
                            break
                    
                    existing_doc_map = {}
                    for key in existing_keys:
                        doc_name = await redis.hget(key, "doc_name")
                        if doc_name:
                            existing_doc_map[doc_name] = key

                    # 3. Delete keys that are no longer expected
                    stale_keys = [key for name, key in existing_doc_map.items() if name not in expected_docs]
                    if stale_keys:
                        logger.info(f"[Local Redis Sync] Deleting {len(stale_keys)} stale keys from Redis")
                        await redis.delete(*stale_keys)

                    # 4. Upsert/Update keys that are expected
                    for doc_name, content in expected_docs.items():
                        try:
                            embedding = await EmbeddingClient.embed_text(content, use_global=True)
                            await MetadataIndexService.upsert_vector(
                                dataset_id=dataset_id,
                                doc_name=doc_name,
                                content=content,
                                embedding=embedding
                            )
                        except Exception as emb_err:
                            logger.error(f"[Local Redis Sync] Embedding failed for doc {doc_name}: {emb_err}")

                    logger.info(f"[Local Redis Sync] Completed sync for dataset: {dataset.name}")

                    from datetime import datetime
                    from sqlalchemy import update
                    from app.models.metadata import MetaDataset

                    await db.execute(
                        update(MetaDataset)
                        .where(MetaDataset.id == dataset_id)
                        .values(
                            rag_sync_status=2,
                            rag_sync_notes="local-redis synced",
                            rag_synced_at=datetime.now(),
                        )
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"[Local Redis Sync] Background task failed for dataset {dataset_id}: {e}", exc_info=True)

        try:
            asyncio.create_task(_run_sync())
        except Exception as e:
            logger.warning(f"[Local Redis Sync] Failed to trigger background sync: {e}")

    @staticmethod
    async def search_knn(
        query_embedding: List[float],
        authorized_dataset_ids: Optional[List[int]],
        top_k: int = 5,
        trace: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute HNSW KNN search with authorized_dataset_ids TAG pre-filtering.
        If authorized_dataset_ids is None, we assume superuser (Admin) and perform search without TAG filter.
        """
        redis = await get_redis_binary()
        if not redis:
            MetadataIndexService._append_trace(
                trace,
                "[KNN] Aborted: Redis binary client unavailable (check REDIS_ENABLE / connection)",
            )
            return []

        vec_dim = len(query_embedding)
        expected_dim = await EmbeddingClient.get_dimensions(use_global=True)
        MetadataIndexService._append_trace(
            trace,
            f"[KNN] Query vector dim={vec_dim}, configured embed_dimensions={expected_dim}, top_k={top_k}",
        )
        if vec_dim != expected_dim:
            MetadataIndexService._append_trace(
                trace,
                f"[WARN] Query vector dim ({vec_dim}) != embed_dimensions ({expected_dim})",
            )

        idx = await MetadataIndexService.index_name()
        index_ready = await MetadataIndexService.ensure_index()
        MetadataIndexService._append_trace(trace, f"[KNN] ensure_index({idx}) => {index_ready}")

        if authorized_dataset_ids is None:
            query_expr = f"*=>[KNN {top_k} @embedding $vec AS score]"
            MetadataIndexService._append_trace(trace, "[KNN] Filter: admin — search all datasets")
        else:
            if not authorized_dataset_ids:
                MetadataIndexService._append_trace(trace, "[KNN] Aborted: empty authorized_dataset_ids")
                return []
            tag_vals = "|".join(_tag_escape(str(ds_id)) for ds_id in authorized_dataset_ids)
            query_expr = f"(@dataset_id:{{{tag_vals}}})=>[KNN {top_k} @embedding $vec AS score]"
            MetadataIndexService._append_trace(
                trace,
                f"[KNN] Filter: dataset_id in {authorized_dataset_ids}",
            )

        MetadataIndexService._append_trace(trace, f"[KNN] FT.SEARCH expr: {query_expr}")

        try:
            raw = await redis.execute_command(
                "FT.SEARCH",
                idx,
                query_expr,
                "PARAMS",
                "2",
                "vec",
                _vector_to_bytes(query_embedding),
                "SORTBY",
                "score",
                "RETURN",
                "4",
                "dataset_id",
                "doc_name",
                "content",
                "score",
                "DIALECT",
                "2",
            )
        except Exception as exc:
            MetadataIndexService._append_trace(trace, f"[KNN] FT.SEARCH error: {exc}")
            raise

        raw_total = 0
        if isinstance(raw, (list, tuple)) and raw:
            try:
                raw_total = int(raw[0])
            except (TypeError, ValueError):
                raw_total = 0
        MetadataIndexService._append_trace(trace, f"[KNN] FT.SEARCH raw total={raw_total}")

        items = MetadataIndexService._parse_knn_response(raw)
        MetadataIndexService._append_trace(trace, f"[KNN] Parsed result count={len(items)}")
        for i, item in enumerate(items):
            sim = item.get("similarity", 0.0)
            MetadataIndexService._append_trace(
                trace,
                f"[KNN] raw[{i + 1}] doc={item.get('doc_name')} "
                f"dataset_id={item.get('dataset_id')} similarity={sim:.4f}",
            )

        if raw_total == 0:
            MetadataIndexService._append_trace(
                trace,
                "[HINT] 0 raw KNN hits — check: (1) same Redis as FT.INFO "
                "(2) embedding dim matches index (3) HSTRLEN embedding = dim*4",
            )

        return items

    @staticmethod
    def _parse_knn_response(raw: Any) -> List[Dict[str, Any]]:
        if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return []
        items: List[Dict[str, Any]] = []
        pos = 1
        while pos + 1 < len(raw):
            fields = raw[pos + 1]
            if not isinstance(fields, (list, tuple)):
                pos += 2
                continue
            row: Dict[str, Any] = {}
            for i in range(0, len(fields) - 1, 2):
                name = MetadataIndexService._hash_field_name(fields[i])
                value = MetadataIndexService._hash_text_value(fields[i + 1])
                row[name] = value
            
            if "score" in row:
                try:
                    row["similarity"] = max(0.0, 1.0 - float(row["score"]))
                except (TypeError, ValueError):
                    row["similarity"] = 0.0
            else:
                row["similarity"] = 0.0
            
            items.append(row)
            pos += 2
        return items

    @staticmethod
    def _hash_field_name(key: Any) -> str:
        if isinstance(key, bytes):
            return key.decode("utf-8", errors="replace")
        return str(key)

    @staticmethod
    def _hash_text_value(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value) if value is not None else ""

    @staticmethod
    async def sync_all_datasets() -> None:
        """
        Synchronize all enabled datasets to Redis vectors.
        """
        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService
        
        async with AsyncSessionLocal() as db:
            try:
                datasets = await MetadataService.get_datasets(db)
                logger.info("[MetadataIndex] Found %d total datasets for startup sync", len(datasets))
                for ds in datasets:
                    if ds.status == 1:
                        await MetadataIndexService.sync_local_redis_vector(ds.id)
            except Exception as e:
                logger.error("[MetadataIndex] Failed during sync_all_datasets: %s", e)
