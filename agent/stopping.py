import json
import math
import time
from collections import defaultdict
from typing import Annotated, Callable

from pydantic import Field

from agent.schemas import Contract, RunStatus


class StopLimits(Contract):
    max_steps: Annotated[int, Field(gt=0)] = 10
    max_retries_per_tool: Annotated[int, Field(ge=0)] = 2
    max_consecutive_same_calls: Annotated[int, Field(gt=0)] = 2
    max_wall_time_seconds: Annotated[float, Field(gt=0)] | None = 120.0
    max_cost: Annotated[float, Field(gt=0)] | None = None


class StopExecution(RuntimeError):
    def __init__(self, reason: str, status: RunStatus):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class StopController:
    def __init__(self, limits: StopLimits | None = None, *, clock: Callable = time.monotonic):
        self.limits = limits or StopLimits()
        self._clock = clock
        self._started = clock()
        self._steps = 0
        self._cost = 0.0
        self._retries = defaultdict(int)
        self._last_call = None
        self._same_calls = 0
        self._tool_available = False
        self._stopped = None

    @property
    def steps(self):
        return self._steps

    @property
    def cost(self):
        return self._cost

    def _stop(self, reason, status):
        self._stopped = StopExecution(reason, status)
        raise self._stopped

    def checkpoint(self):
        if self._stopped is not None:
            raise self._stopped
        if (self.limits.max_wall_time_seconds is not None
                and self._clock() - self._started >= self.limits.max_wall_time_seconds):
            self._stop("TOOL_TIMEOUT", "TIMEOUT")
        if self.limits.max_cost is not None and self._cost >= self.limits.max_cost:
            self._stop("COST_LIMIT", "FAILED")

    def remaining_seconds(self):
        self.checkpoint()
        if self.limits.max_wall_time_seconds is None:
            return None
        return max(0.0, self.limits.max_wall_time_seconds - (self._clock() - self._started))

    def begin_decision(self):
        self.checkpoint()
        if self._steps >= self.limits.max_steps:
            self._stop("MAX_STEPS", "MAX_STEPS")
        self._steps += 1
        self._tool_available = True
        return self._steps

    def before_tool_call(self, tool, arguments, *, retry=False):
        self.checkpoint()
        if not self._tool_available:
            raise RuntimeError("begin_decision is required before each tool attempt")
        self._tool_available = False
        signature = json.dumps(
            [tool, arguments], sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        same_calls = self._same_calls + 1 if signature == self._last_call else 1
        if same_calls > self.limits.max_consecutive_same_calls:
            self._stop("LOOP_DETECTED", "FAILED")
        if retry and self._retries[tool] >= self.limits.max_retries_per_tool:
            self._stop("RETRY_LIMIT", "FAILED")
        if retry:
            self._retries[tool] += 1
        self._last_call = signature
        self._same_calls = same_calls

    def record_cost(self, amount):
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ValueError("cost must be finite and nonnegative")
        self.checkpoint()
        self._cost += amount
        self.checkpoint()

    def cancel(self):
        if self._stopped is not None:
            raise self._stopped
        self._stop("USER_CANCELLED", "CANCELLED")
