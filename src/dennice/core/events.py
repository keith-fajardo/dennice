from collections.abc import AsyncIterator

from dennice.core.models import RunEvent


class EventStream:
    """Small in-process event stream used by the harness and presentation layers."""

    def __init__(self) -> None:
        self._events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> RunEvent:
        self._events.append(event)
        return event

    async def replay(self) -> AsyncIterator[RunEvent]:
        for event in self._events:
            yield event

    @property
    def events(self) -> list[RunEvent]:
        return list(self._events)
