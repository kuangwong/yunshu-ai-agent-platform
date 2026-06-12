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
    async def ensure_index() -> bool:
        redis = await get_redis()
        if not redis:
            return False
        idx = await MetadataIndexService.index_name()
        dim = await EmbeddingClient.get_dimensions()
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
                            embedding = await EmbeddingClient.embed_text(content)
                            await MetadataIndexService.upsert_vector(
                                dataset_id=dataset_id,
                                doc_name=doc_name,
                                content=content,
                                embedding=embedding
                            )
                        except Exception as emb_err:
                            logger.error(f"[Local Redis Sync] Embedding failed for doc {doc_name}: {emb_err}")

                    logger.info(f"[Local Redis Sync] Completed sync for dataset: {dataset.name}")
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
    ) -> List[Dict[str, Any]]:
        """
        Execute HNSW KNN search with authorized_dataset_ids TAG pre-filtering.
        If authorized_dataset_ids is None, we assume superuser (Admin) and perform search without TAG filter.
        """
        redis = await get_redis_binary()
        if not redis:
            return []
        
        idx = await MetadataIndexService.index_name()
        await MetadataIndexService.ensure_index()

        if authorized_dataset_ids is None:
            query_expr = f"*=>[KNN {top_k} @embedding $vec AS score]"
        else:
            if not authorized_dataset_ids:
                return []
            tag_vals = "|".join(_tag_escape(str(ds_id)) for ds_id in authorized_dataset_ids)
            query_expr = f"(@dataset_id:{{{tag_vals}}})=>[KNN {top_k} @embedding $vec AS score]"

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
        return MetadataIndexService._parse_knn_response(raw)

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
