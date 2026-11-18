"""LangGraph orchestration shared by the CLI, dashboard, and offline evaluator."""
import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from agent.stopping import StopLimits


class WorkflowState(TypedDict, total=False):
    next_stage: str
    status: str


# Each node resumes the controller at one well-defined operation. Only the
# route and terminal status enter LangSmith. The transcript and grant stay local.
TRANSITIONS = {
    "initialize": ("decision",),
    "decision": ("investigation", "proposal", "decision"),
    "investigation": ("decision", "retry"),
    "retry": ("investigation", "proposal"),
    "proposal": ("approval",),
    "approval": ("write", "decision", "retry"),
    "write": ("email", "verification", "retry"),
    "email": ("verification",),
    "verification": ("decision",),
}


class TraceOutcomeError(RuntimeError):
    """A sanitized agent failure reported to the graph tracer."""


def run_triage(agent, request, **options):
    steps = agent.workflow_steps(request, **options)
    result = None
    failure = None

    def advance(_state):
        nonlocal result, failure
        try:
            return {"next_stage": next(steps)}
        except StopIteration as completed:
            result = completed.value
            if result.status in {"FAILED", "TIMEOUT", "MAX_STEPS"}:
                raise TraceOutcomeError(f"Operator triage ended with {result.status}") from None
            return {"next_stage": END, "status": result.status}
        except Exception as error:
            failure = error
            raise TraceOutcomeError("Operator triage failed unexpectedly") from None

    graph = StateGraph(WorkflowState)
    for stage, destinations in TRANSITIONS.items():
        graph.add_node(stage, advance)
        graph.add_conditional_edges(stage, lambda state: state["next_stage"], [*destinations, END])
    graph.add_edge(START, "initialize")

    limits = agent.limits or StopLimits()
    config = {"run_name": "Operator triage", "recursion_limit": 8 * limits.max_steps + 4}
    tracing_enabled = os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
    try:
        with tracing_context(enabled=tracing_enabled):
            graph.compile().invoke({}, config=config)
    except TraceOutcomeError:
        # The graph records the error, while callers retain their existing result contract.
        pass
    finally:
        steps.close()
    if failure is not None:
        raise failure
    return result
