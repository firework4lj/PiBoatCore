from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SensorAdapter(ABC):
    name: str

    @abstractmethod
    async def read(self) -> dict[str, Any]:
        raise NotImplementedError
