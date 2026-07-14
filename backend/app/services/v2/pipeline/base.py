"""Pipeline 抽象基类"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PipelineContext:
    dataset_id: str
    version_no: int
    route: str  # A | B | C
    spec: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    result_uri: str | None = None
    rows_in: int = 0
    rows_out: int = 0
    error: str | None = None

class PipelineStep(ABC):
    @abstractmethod
    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        """转换数据并返回新数据"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__
