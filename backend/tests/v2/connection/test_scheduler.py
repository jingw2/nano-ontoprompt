"""CronService 单元测试"""
import pytest
from app.services.v2.scheduler.cron_service import CronService


def test_validate_cron_valid():
    svc = CronService()
    assert svc.validate_cron("* * * * *") is True
    assert svc.validate_cron("0 8 * * *") is True
    assert svc.validate_cron("*/5 * * * *") is True
    assert svc.validate_cron("0 0 * * 1-5") is True


def test_validate_cron_invalid():
    svc = CronService()
    assert svc.validate_cron("invalid") is False
    assert svc.validate_cron("* * *") is False
    assert svc.validate_cron("") is False


def test_parse_cron_returns_celery_params():
    svc = CronService()
    params = svc.parse_cron("0 8 * * *")
    assert params["minute"] == "0"
    assert params["hour"] == "8"
    assert params["day_of_month"] == "*"


def test_parse_cron_invalid_raises():
    svc = CronService()
    with pytest.raises(ValueError, match="无效"):
        svc.parse_cron("bad expression")


def test_schedule_connection_returns_dict():
    svc = CronService()
    result = svc.schedule_connection_sync("conn-1", "0 8 * * *")
    assert result["status"] == "scheduled"
    assert result["connection_id"] == "conn-1"
    assert "celery_crontab" in result


def test_schedule_pipeline_returns_dict():
    svc = CronService()
    result = svc.schedule_pipeline_run("pl-1", "*/30 * * * *")
    assert result["status"] == "scheduled"
    assert result["pipeline_id"] == "pl-1"


def test_describe_cron_every_minute():
    svc = CronService()
    assert svc.describe_cron("* * * * *") == "每分钟"


def test_describe_cron_daily_8am():
    svc = CronService()
    assert svc.describe_cron("0 8 * * *") == "每天 08:00"


def test_describe_cron_invalid():
    svc = CronService()
    assert svc.describe_cron("bad") == "无效的 cron 表达式"
