from collections.abc import AsyncIterator

from dennice.core.models import EventKind, ExecutionRequest, RunEvent


class MockExecutor:
    """Offline executor that demonstrates streaming without pretending to query user systems."""

    id = "mock"
    version = "v1"

    async def execute(self, run_id: str, request: ExecutionRequest) -> AsyncIterator[RunEvent]:
        chunks = [
            "This offline executor has no connected data tools, so it cannot establish a root cause. "
            "A real investigation should first collect the evidence required by the selected policies.\n\n",
            "Suggested next step: inspect relevant history, changes, and component-level measurements before remediation.",
        ]
        for chunk in chunks:
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": chunk})
