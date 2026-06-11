import pytest
from datetime import datetime

import pytz

from app.services.ai.time_anchor import build_data_query_time_anchor_block

pytestmark = pytest.mark.no_infrastructure


def test_build_data_query_time_anchor_block_uses_anchor_dates():
    tz = pytz.timezone("Asia/Shanghai")
    now = tz.localize(datetime(2026, 6, 10, 14, 30, 0))

    block = build_data_query_time_anchor_block(now=now)

    assert "[当前时间锚点]" in block
    assert "当前时刻：2026-06-10 14:30:00 星期三" in block
    assert "今天：2026-06-10" in block
    assert "昨天：2026-06-09" in block
    assert "本周（周一至今天）：2026-06-08 至 2026-06-10" in block
    assert "上周（周一至周日）：2026-06-01 至 2026-06-07" in block
    assert "本月/当月（月初至今天）：2026-06-01 至 2026-06-10" in block
    assert "本月完整自然月：2026-06-01 至 2026-06-30" in block
    assert "上月：2026-05-01 至 2026-05-31" in block
    assert "今年（年初至今天）：2026-01-01 至 2026-06-10" in block
    assert "去年：2025-01-01 至 2025-12-31" in block
    assert "禁止臆测年份或月份" in block


def test_build_data_query_time_anchor_block_year_boundary_month():
    tz = pytz.timezone("Asia/Shanghai")
    now = tz.localize(datetime(2026, 1, 5, 9, 0, 0))

    block = build_data_query_time_anchor_block(now=now)

    assert "上月：2025-12-01 至 2025-12-31" in block
    assert "本周（周一至今天）：2026-01-05 至 2026-01-05" in block
