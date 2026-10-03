from collections.abc import AsyncIterator
from typing import Protocol

from dennice.core.models import ExecutionRequest, RunEvent


class Executor(Protocol):
    id: str
    version: str

    async def execute(self, run_id: str, request: ExecutionRequest) -> AsyncIterator[RunEvent]: ...
