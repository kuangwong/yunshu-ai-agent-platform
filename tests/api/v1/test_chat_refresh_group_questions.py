import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from unittest.mock import AsyncMock, patch
from app.services.auth_service import AuthService

@pytest.mark.asyncio
async def test_refresh_group_questions_api_success(db_session):
    user_key = await AuthService.generate_api_key("test_refresh_user_api", role="user", db=db_session)
    
    mock_questions = [
        {"label": "新问题1", "query": "指令1", "type": "dynamic"},
        {"label": "新问题2", "query": "指令2", "type": "dynamic"}
    ]
    
    with patch(
        "app.services.dataset_navigation_service.DatasetNavigationService.refresh_group_questions",
        AsyncMock(return_value=mock_questions)
    ) as mock_refresh:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "group_title": "智能体运行分析",
                "tables": ["智能体访问日志"]
            }
            resp = await client.post(
                "/api/v1/chat/dataset-menu/refresh-group-questions",
                json=payload,
                headers={"X-API-Key": user_key}
            )
            
            assert resp.status_code == 200
            data = resp.json()
            assert data["code"] == 200
            assert data["message"] == "success"
            assert len(data["data"]["questions"]) == 2
            assert data["data"]["questions"][0]["label"] == "新问题1"
            assert data["data"]["questions"][0]["query"] == "指令1"
            
            mock_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_group_followups_api_success(db_session):
    user_key = await AuthService.generate_api_key("test_refresh_followups_api", role="user", db=db_session)

    mock_followups = [
        {"label": "字段口径", "query": "说明字段口径", "type": "dynamic"},
        {"label": "关联分析", "query": "还能关联哪些表", "type": "dynamic"},
    ]

    with patch(
        "app.services.dataset_navigation_service.DatasetNavigationService.refresh_group_followups",
        AsyncMock(return_value=mock_followups),
    ) as mock_refresh:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/dataset-menu/refresh-group-questions",
                json={
                    "group_title": "智能体元数据",
                    "tables": ["智能体访问日志"],
                    "purpose": "followups",
                },
                headers={"X-API-Key": user_key},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["code"] == 200
            assert len(data["data"]["questions"]) == 2
            mock_refresh.assert_awaited_once()
