from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.contracts import SourceRecord


class SourceReader(ABC):
    @abstractmethod
    def read(self) -> list[SourceRecord]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def supports(cls, source: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def _to_row_count(rows: list[dict[str, Any]]) -> int:
        return len(rows)
