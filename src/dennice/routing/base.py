from typing import Protocol

from dennice.core.models import RoutingDecision, Task


class CognitiveRouter(Protocol):
    id: str
    version: str

    async def classify(self, task: Task) -> RoutingDecision: ...
