"""Shared LangGraph entry point for issue triage."""
import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context


class WorkflowState(TypedDict, total=False):
    status: str


def run_triage(agent, request, **options):
    # Keep issue data, tool results, and approval details outside traced state.
    result = None
    failure = None

    def triage(_state):
        nonlocal result, failure
        try:
            result = agent.run(request, **options)
        except Exception as error:
            failure = error
            return {"status": "FAILED"}
        return {"status": result.status}

    graph = StateGraph(WorkflowState)
    graph.add_node("triage", triage)
    graph.add_edge(START, "triage")
    graph.add_edge("triage", END)

    tracing_enabled = os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
    with tracing_context(enabled=tracing_enabled):
        graph.compile().invoke({}, config={"run_name": "Operator triage"})
    if failure is not None:
        raise failure
    return result
