import pytest

from app.schemas.user_sync import ThirdPartyUserSyncConfig, ThirdPartyUserSyncFieldMap
from app.services.user_sync_service import UserSyncService


def test_build_select_sql_mysql():
    field_map = ThirdPartyUserSyncFieldMap(
        id="user_id",
        user_name="login_name",
        real_name="display_name",
        remark="dept",
    )
    sql = UserSyncService._build_select_sql("hr_users", field_map, "mysql")
    assert "FROM `hr_users`" in sql
    assert "`user_id` AS id" in sql
    assert "`login_name` AS user_name" in sql


def test_normalize_external_user():
    row = {"id": "100", "user_name": " alice ", "real_name": "Bob", "remark": None}
    result = UserSyncService._normalize_external_user(row)
    assert result == {
        "id": 100,
        "user_name": "alice",
        "real_name": "Bob",
        "remark": None,
    }


def test_schedule_to_cron():
    assert UserSyncService.schedule_to_cron("hourly") == {"minute": 0}
    assert UserSyncService.schedule_to_cron("daily") == {"hour": 2, "minute": 0}
    assert UserSyncService.schedule_to_cron("off") is None


def test_default_config_roundtrip():
    cfg = UserSyncService._default_config()
    assert cfg.enabled is False
    assert cfg.schedule == "off"
