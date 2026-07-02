from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


SchedulePreset = Literal["off", "hourly", "daily", "weekly"]


class ThirdPartyUserSyncFieldMap(BaseModel):
    id: str = Field(..., description="第三方用户 ID 列")
    user_name: str = Field(..., description="第三方用户名列")
    real_name: Optional[str] = None
    remark: Optional[str] = None


class ThirdPartyUserSyncConfig(BaseModel):
    enabled: bool = False
    connection_config_id: Optional[int] = None
    table_name: Optional[str] = None
    field_map: ThirdPartyUserSyncFieldMap = Field(
        default_factory=lambda: ThirdPartyUserSyncFieldMap(id="", user_name="")
    )
    schedule: SchedulePreset = "off"


class ThirdPartyUserSyncConfigUpdate(ThirdPartyUserSyncConfig):
    pass


class ThirdPartyUserSyncRunRequest(BaseModel):
    user_ids: Optional[List[int]] = Field(
        default=None,
        description="指定同步的用户 ID 列表；为空则同步全部待同步用户",
    )
