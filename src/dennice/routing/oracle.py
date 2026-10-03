from dennice.core.models import RoutingDecision, Task


class OracleRouter:
    """Deterministic benchmark router keyed by task ID; never used as a general classifier."""

    id = "oracle"
    version = "v1"

    def __init__(self, decisions: dict[str, RoutingDecision]) -> None:
        self._decisions = decisions

    async def classify(self, task: Task) -> RoutingDecision:
        try:
            decision = self._decisions[task.id]
        except KeyError as exc:
            raise KeyError(f"No oracle routing decision for task {task.id!r}") from exc
        return decision.model_copy(update={"router_id": self.id, "router_version": self.version})
