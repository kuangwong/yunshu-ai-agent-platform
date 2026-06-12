"""Unit tests for local Redis vector search and MetadataIndexService."""
import pytest
from unittest.mock import AsyncMock, patch
from app.services.ai.metadata_index_service import MetadataIndexService

pytestmark = pytest.mark.no_infrastructure

@pytest.mark.asyncio
async def test_metadata_index_ensure_index():
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(side_effect=Exception("Index does not exist"))
    
    with patch("app.services.ai.metadata_index_service.get_redis", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.EmbeddingClient.get_dimensions", return_value=1536):
        
        # Calling ensure_index should trigger FT.CREATE
        res = await MetadataIndexService.ensure_index()
        assert res is True
        mock_redis.execute_command.assert_any_call(
            "FT.CREATE",
            "yunshu:idx:metadata:dataset",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "metadata:dataset:",
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
            "1536",
            "DISTANCE_METRIC",
            "COSINE",
        )

@pytest.mark.asyncio
async def test_metadata_index_upsert_vector():
    mock_redis = AsyncMock()
    
    with patch("app.services.ai.metadata_index_service.get_redis", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True):
        
        await MetadataIndexService.upsert_vector(
            dataset_id=42,
            doc_name="res_user.txt",
            content="columns: []",
            embedding=[0.1, 0.2, 0.3]
        )
        
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args[0]
        assert call_args[0] == "metadata:dataset:42:table:res_user"
        mapping = mock_redis.hset.call_args[1]["mapping"]
        assert mapping["dataset_id"] == "42"
        assert mapping["doc_name"] == "res_user.txt"
        assert mapping["content"] == "columns: []"
        assert "embedding" in mapping

@pytest.mark.asyncio
async def test_metadata_index_delete_dataset_vectors():
    mock_redis = AsyncMock()
    mock_redis.scan = AsyncMock(side_effect=[
        (0, ["metadata:dataset:42:table:res_user", "metadata:dataset:42:metrics"]),
    ])
    
    with patch("app.services.ai.metadata_index_service.get_redis", return_value=mock_redis):
        await MetadataIndexService.delete_dataset_vectors(42)
        mock_redis.delete.assert_called_once_with(
            "metadata:dataset:42:table:res_user", 
            "metadata:dataset:42:metrics"
        )

@pytest.mark.asyncio
async def test_metadata_index_search_knn_admin():
    mock_redis = AsyncMock()
    raw_response = [
        1,
        "metadata:dataset:42:table:res_user",
        [
            b"dataset_id", b"42",
            b"doc_name", b"res_user.txt",
            b"content", b"columns: []",
            b"score", b"0.15"
        ]
    ]
    mock_redis.execute_command = AsyncMock(return_value=raw_response)
    
    with patch("app.services.ai.metadata_index_service.get_redis_binary", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True):
        
        results = await MetadataIndexService.search_knn(
            query_embedding=[0.1] * 1536,
            authorized_dataset_ids=None, # Admin
            top_k=5
        )
        
        assert len(results) == 1
        assert results[0]["dataset_id"] == "42"
        assert results[0]["doc_name"] == "res_user.txt"
        assert results[0]["similarity"] == pytest.approx(0.85) # 1.0 - 0.15
        
        query_expr = mock_redis.execute_command.call_args[0][2]
        assert query_expr == "*=>[KNN 5 @embedding $vec AS score]"

@pytest.mark.asyncio
async def test_metadata_index_search_knn_normal_user():
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=[])
    
    with patch("app.services.ai.metadata_index_service.get_redis_binary", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True):
        
        await MetadataIndexService.search_knn(
            query_embedding=[0.1] * 1536,
            authorized_dataset_ids=[1, 3], # Normal user
            top_k=3
        )
        
        query_expr = mock_redis.execute_command.call_args[0][2]
        assert query_expr == "(@dataset_id:{1|3})=>[KNN 3 @embedding $vec AS score]"

@pytest.mark.asyncio
async def test_schema_endpoint_local_vector_search():
    from app.api.v1.endpoints.schema import get_database_schema, SchemaRequest
    from app.models.metadata import MetaDataset
    
    mock_conn = AsyncMock()
    mock_user = {"user_id": 42, "role": "user"}
    
    dataset = MetaDataset(id=10, name="test_ds", display_name="Test DS")
    
    redis_results = [
        {
            "dataset_id": "10",
            "doc_name": "test_table.txt",
            "content": "table definition here",
            "similarity": 0.85
        }
    ]
    
    request = SchemaRequest(query="test query")
    
    with patch("app.services.metadata_service.MetadataService.search_datasets", new_callable=AsyncMock, return_value=[dataset]), \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock, return_value=[0.1]*1536), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.search_knn", new_callable=AsyncMock, return_value=redis_results):
        
        resp = await get_database_schema(
            request=request,
            conn=mock_conn,
            current_user=mock_user
        )
        
        assert resp.data.provider == "local"
        assert "test_table.txt" in resp.data.schema_context
        assert "Sim: 0.85" in resp.data.schema_context
        assert len(resp.data.hits) == 1
        assert resp.data.hits[0].id == 10
        assert resp.data.hits[0].name == "test_ds"

