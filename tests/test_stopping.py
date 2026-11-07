import pytest
from pydantic import ValidationError

from agent.stopping import StopController, StopExecution, StopLimits


def test_100_invalid_model_decisions_stop_at_10():
    controller = StopController()
    executed = []
    for _ in range(100):
        try:
            executed.append(controller.begin_decision())
        except StopExecution as error:
            assert error.reason == "MAX_STEPS"
            break
    assert executed == list(range(1, 11))
    assert controller.steps == 10
    with pytest.raises(StopExecution, match="MAX_STEPS"):
        controller.begin_decision()


def test_100_changing_tool_calls_still_stop_at_10():
    controller = StopController()
    calls = []
    for issue_id in range(1, 101):
        try:
            controller.begin_decision()
            controller.before_tool_call("read_issue", {"issue_id": issue_id})
            calls.append(issue_id)
        except StopExecution as error:
            assert error.status == "MAX_STEPS"
            break
    assert len(calls) == controller.steps == 10


def test_repeated_call_stops_before_third_execution():
    controller = StopController()
    calls = 0
    for _ in range(100):
        try:
            controller.begin_decision()
            controller.before_tool_call("read_issue", {"issue_id": 1})
            calls += 1
        except StopExecution as error:
            assert error.reason == "LOOP_DETECTED"
            break
    assert calls == 2
    assert controller.steps == 3


def test_argument_key_order_does_not_bypass_repeat_detection():
    controller = StopController()
    for args in [{"a": 1, "b": 2}, {"b": 2, "a": 1}]:
        controller.begin_decision()
        controller.before_tool_call("example", args)
    controller.begin_decision()
    with pytest.raises(StopExecution, match="LOOP_DETECTED"):
        controller.before_tool_call("example", {"a": 1, "b": 2})


def test_retries_are_bounded_per_tool_and_consume_decisions():
    controller = StopController(StopLimits(max_consecutive_same_calls=10))
    for retry in [False, True, True]:
        controller.begin_decision()
        controller.before_tool_call("read_issue", {"issue_id": 1}, retry=retry)
    controller.begin_decision()
    with pytest.raises(StopExecution, match="RETRY_LIMIT"):
        controller.before_tool_call("read_issue", {"issue_id": 1}, retry=True)
    assert controller.steps == 4


def test_retry_budget_survives_intervening_calls():
    controller = StopController(StopLimits(max_retries_per_tool=1))
    controller.begin_decision()
    controller.before_tool_call("read_issue", {"issue_id": 1}, retry=True)
    controller.begin_decision()
    controller.before_tool_call("get_labels", {})
    controller.begin_decision()
    with pytest.raises(StopExecution, match="RETRY_LIMIT"):
        controller.before_tool_call("read_issue", {"issue_id": 2}, retry=True)


def test_tool_requires_reserved_decision_and_cannot_run_twice():
    controller = StopController()
    with pytest.raises(RuntimeError):
        controller.before_tool_call("get_labels", {})
    controller.begin_decision()
    controller.before_tool_call("get_labels", {})
    with pytest.raises(RuntimeError):
        controller.before_tool_call("get_labels", {})


@pytest.mark.parametrize("boundary", ["model", "tool"])
def test_deadline_blocks_calls_at_boundary_without_sleeping(boundary):
    now = [10.0]
    controller = StopController(clock=lambda: now[0])
    assert controller.remaining_seconds() == 120
    if boundary == "tool":
        controller.begin_decision()
    now[0] = 130.0
    with pytest.raises(StopExecution) as raised:
        if boundary == "model":
            controller.begin_decision()
        else:
            controller.before_tool_call("get_labels", {})
    assert raised.value.status == "TIMEOUT"
    now[0] = 10.0
    with pytest.raises(StopExecution):
        controller.begin_decision()


def test_deadline_can_be_disabled_but_step_bound_still_applies():
    controller = StopController(StopLimits(max_steps=1, max_wall_time_seconds=None))
    assert controller.remaining_seconds() is None
    controller.begin_decision()
    with pytest.raises(StopExecution, match="MAX_STEPS"):
        controller.begin_decision()


def test_spend_limit_blocks_further_work():
    controller = StopController(StopLimits(max_cost=1.0))
    controller.record_cost(0.5)
    controller.begin_decision()
    with pytest.raises(StopExecution, match="COST_LIMIT"):
        controller.record_cost(0.5)
    assert controller.cost == 1
    with pytest.raises(StopExecution, match="COST_LIMIT"):
        controller.before_tool_call("get_labels", {})


@pytest.mark.parametrize("amount", [-1, True, "1", float("nan"), float("inf")])
def test_invalid_cost_rejected(amount):
    controller = StopController()
    with pytest.raises(ValueError):
        controller.record_cost(amount)
    assert controller.cost == 0


def test_cancel_is_terminal():
    controller = StopController()
    controller.begin_decision()
    with pytest.raises(StopExecution, match="USER_CANCELLED"):
        controller.cancel()
    with pytest.raises(StopExecution):
        controller.before_tool_call("get_labels", {})
    with pytest.raises(StopExecution):
        controller.begin_decision()


@pytest.mark.parametrize("limits", [
    {"max_steps": 0}, {"max_steps": True}, {"max_retries_per_tool": -1},
    {"max_consecutive_same_calls": 0}, {"max_wall_time_seconds": 0},
    {"max_wall_time_seconds": float("nan")}, {"max_cost": float("inf")},
])
def test_invalid_limits_rejected(limits):
    with pytest.raises(ValidationError):
        StopLimits(**limits)
