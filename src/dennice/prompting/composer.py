from dennice.core.models import ExecutionRequest, ReasoningPolicy, RoutingDecision, Task


class DefaultPromptComposer:
    """Composes observable policy guidance without requesting hidden reasoning traces."""

    version = "v1"

    def __init__(self, base_instructions: str | None = None) -> None:
        self.base_instructions = base_instructions or (
            "You are a data analytics execution assistant. Produce an actionable response. "
            "Use available evidence, distinguish it from hypotheses, and never fabricate tool results."
        )

    def compose(
        self,
        task: Task,
        decision: RoutingDecision | None,
        policies: list[ReasoningPolicy],
    ) -> ExecutionRequest:
        sections = ["BASE EXECUTION INSTRUCTIONS\n" + self.base_instructions]
        if policies:
            primary = policies[0]
            sections.append(
                f"PRIMARY REASONING POLICY: {primary.cognitive_demand.value} ({primary.id})\n"
                + primary.instructions
            )
            for policy in policies[1:]:
                sections.append(
                    f"SUPPORTING REASONING POLICY: {policy.cognitive_demand.value} ({policy.id})\n"
                    + policy.instructions
                )
        if decision:
            sections.append(f"ROUTING CONTEXT\nTask family: {decision.task_family}")
        return ExecutionRequest(
            task=task,
            system_instructions="\n\n".join(sections),
            policies=policies,
            executor_id="mock",
        )
