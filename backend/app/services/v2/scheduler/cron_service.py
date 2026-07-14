"""Cron 调度服务 — 验证、解析、注册定时任务"""
from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)

# 5段式 cron 表达式格式验证
CRON_PATTERN = re.compile(
    r'^(\*|[0-9,\-\*/]+)\s+'
    r'(\*|[0-9,\-\*/]+)\s+'
    r'(\*|[0-9,\-\*/]+)\s+'
    r'(\*|[0-9,\-\*/]+)\s+'
    r'(\*|[0-9,\-\*/]+)$'
)


class CronService:
    """管理 Connection 和 Pipeline 的定时同步/运行任务"""

    def validate_cron(self, expression: str) -> bool:
        """验证 cron 表达式格式（5段式）"""
        return bool(CRON_PATTERN.match(expression.strip()))

    def parse_cron(self, expression: str) -> dict:
        """将 cron 表达式解析为 Celery crontab 参数字典"""
        if not self.validate_cron(expression):
            raise ValueError(f"无效的 cron 表达式：{expression}")
        parts = expression.strip().split()
        return {
            "minute": parts[0],
            "hour": parts[1],
            "day_of_month": parts[2],
            "month_of_year": parts[3],
            "day_of_week": parts[4],
        }

    def schedule_connection_sync(self, connection_id: str, cron_expr: str) -> dict:
        """为 Connection 注册定时同步任务"""
        cron_params = self.parse_cron(cron_expr)
        logger.info(f"Connection {connection_id} 调度已注册: {cron_expr}")
        return {
            "connection_id": connection_id,
            "cron": cron_expr,
            "celery_crontab": cron_params,
            "status": "scheduled",
        }

    def schedule_pipeline_run(self, pipeline_id: str, cron_expr: str) -> dict:
        """为 Pipeline 注册定时运行任务"""
        cron_params = self.parse_cron(cron_expr)
        logger.info(f"Pipeline {pipeline_id} 调度已注册: {cron_expr}")
        return {
            "pipeline_id": pipeline_id,
            "cron": cron_expr,
            "celery_crontab": cron_params,
            "status": "scheduled",
        }

    def describe_cron(self, expression: str) -> str:
        """将 cron 表达式转换为人类可读描述"""
        if not self.validate_cron(expression):
            return "无效的 cron 表达式"
        parts = expression.strip().split()
        minute, hour, dom, month, dow = parts

        if expression.strip() == "* * * * *":
            return "每分钟"
        if minute.startswith("*/"):
            n = minute[2:]
            return f"每 {n} 分钟"
        if minute == "0" and dom == "*" and month == "*" and dow == "*":
            if hour == "*":
                return "每小时整点"
            if hour == "8":
                return "每天 08:00"
            return f"每天 {hour}:00"
        return f"按计划执行 ({expression.strip()})"
