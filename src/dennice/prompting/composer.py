from dennice.core.models import ExecutionRequest, ReasoningPolicy, RoutingDecision, Task


class DefaultPromptComposer:
    """Composes observable policy guidance without requesting hidden reasoning traces."""

    version = "v2"

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
        if decision:
            sections.append(
                "COGNITIVE ROUTING CONTRACT\n"
                f"Task family: {decision.task_family}\n"
                f"Primary cognitive demand: {decision.primary_demand.value}\n"
                "Supporting cognitive demands: "
                + (", ".join(demand.value for demand in decision.supporting_demands) or "none")
                + "\n\n"
                "The route configures how to investigate and reason; it is not a conclusion about "
                "the task. Follow the selected policies, but do not claim they prove a root cause. "
                "Make the answer auditable: distinguish evidence from hypotheses, state validation "
                "or uncertainty, and give concrete next steps when evidence is unavailable."
            )
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
        history = task.context.get("conversation_history")
        if isinstance(history, list):
            turns = [
                f"{turn['role'].upper()}: {turn['content']}"
                for turn in history[-16:]
                if isinstance(turn, dict)
                and isinstance(turn.get("role"), str)
                and isinstance(turn.get("content"), str)
                and turn["content"].strip()
            ]
            if turns:
                sections.append("CONVERSATION HISTORY\n" + "\n\n".join(turns))
        return ExecutionRequest(
            task=task,
            system_instructions="\n\n".join(sections),
            policies=policies,
            executor_id="mock",
        )
