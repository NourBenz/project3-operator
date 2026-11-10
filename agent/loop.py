"""Bounded plan -> tool -> observe loop with controller-owned approvals."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from agent.llm import ModelError
from agent.approval import ApprovalError
from agent.logging_utils import CallContext
from agent.registry import ToolRegistry
from agent.stopping import StopController, StopExecution
from agent.task import understand_task
from sandbox.mock_github import MockGitHub
from tools.common import failure


WRITE_TOOLS = {"update_labels", "post_comment"}


@dataclass
class AgentResult:
    task_id: str
    status: str
    summary: str
    plan: list[str]
    transcript: list[dict] = field(default_factory=list)
    pending_proposal: object | None = None
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None


class TriageAgent:
    def __init__(self, model, *, service=None, registry=None, limits=None, clock=time.monotonic):
        self.model = model
        self.service = service or MockGitHub()
        self.registry = registry or ToolRegistry()
        self.limits = limits
        self.clock = clock

    def run(self, user_request, *, task_id=None, approval_handler=None, proposal_presenter=None):
        task_id = task_id or "task-" + uuid.uuid4().hex
        controller = StopController(self.limits, clock=self.clock)
        transcript = []
        plan = ["Read the requested issue", "Read allowed labels", "Propose a label update", "Verify any approved write"]
        usage = {"input_tokens": 0, "output_tokens": 0, "model": getattr(self.model, "name", getattr(self.model, "model", "unknown"))}
        scope = understand_task(user_request)
        if scope.clarification:
            return AgentResult(task_id, "FAILED", scope.clarification, plan, transcript, usage=usage)
        try:
            while True:
                step = controller.begin_decision()
                decision = self.model.decide(user_request, transcript, self.registry.openai_tools())
                usage["input_tokens"] += decision.input_tokens or 0
                usage["output_tokens"] += decision.output_tokens or 0
                if decision.tool_calls:
                    if len(decision.tool_calls) != 1:
                        return AgentResult(task_id, "FAILED", "Model attempted multiple actions in one decision; stopped safely.", plan, transcript, usage=usage)
                    call = decision.tool_calls[0]
                    try:
                        args = self.registry.validate(call.name, call.arguments).model_dump(mode="json")
                    except Exception:
                        result = {"tool": call.name, "result": failure("INVALID_ARGUMENTS", "Model requested invalid or disallowed tool arguments")}
                        transcript.append({"kind": "tool_result", "result": result})
                        continue
                    if not self._call_is_in_scope(call.name, args, scope):
                        result = {"tool": call.name, "result": failure(
                            "INVALID_ARGUMENTS", "Model requested an operation outside the user's task scope",
                        )}
                        transcript.append({"kind": "tool_result", "result": result})
                        continue
                    if call.name in WRITE_TOOLS and not self._write_investigation_supported(transcript, args):
                        result = {"tool": call.name, "result": failure(
                            "INVALID_ARGUMENTS",
                            "Read the requested issue and allowed labels before proposing a write",
                        )}
                        transcript.append({"kind": "tool_result", "result": result})
                        continue
                    controller.before_tool_call(call.name, args)
                    result, pending = self._call(
                        call.name, args, task_id, step, approval_handler,
                        issue_context=self._latest_issue(transcript),
                        explanation=decision.explanation,
                        proposal_presenter=proposal_presenter,
                    )
                    transcript.append({"kind": "tool_result", "result": {"tool": call.name, "result": result}})
                    if pending is not None:
                        return AgentResult(task_id, "AWAITING_APPROVAL", "A human decision is required before the proposed write.", plan, transcript, pending, usage)
                    if result["error"] and result["error"].get("retryable"):
                        result = self._retry(call.name, args, task_id, controller, transcript, approval_handler,
                                             proposal_presenter)
                    if call.name in WRITE_TOOLS and result["ok"]:
                        self._verify_successful_write(args, task_id, controller, transcript)
                    continue
                if decision.final:
                    if not self._final_is_supported(transcript, scope):
                        return AgentResult(
                            task_id, "FAILED",
                            "Agent returned a final answer without a completed tool-backed outcome.",
                            plan, transcript, usage=usage,
                        )
                    return AgentResult(task_id, "DONE", decision.final, plan, transcript, usage=usage)
                return AgentResult(task_id, "FAILED", "Model returned neither a tool action nor a final answer.", plan, transcript, usage=usage)
        except StopExecution as error:
            return AgentResult(task_id, error.status, "Agent stopped safely: " + error.reason, plan, transcript, usage=usage, stop_reason=error.reason)
        except ModelError as error:
            return AgentResult(task_id, "FAILED", str(error), plan, transcript, usage=usage)

    def _call(self, name, args, task_id, step, approval_handler, *, issue_context=None,
              explanation=None, proposal_presenter=None):
        context = CallContext(task_id=task_id, step=step)
        if name not in WRITE_TOOLS:
            return self.service.call(name, args, task_id=task_id, context=context), None
        proposal = self.service.propose(name, args, task_id=task_id)
        if proposal_presenter is not None:
            proposal_presenter(proposal, issue_context, explanation)
        if approval_handler is None:
            return failure("APPROVAL_REQUIRED", "Human approval is required"), proposal
        try:
            response = approval_handler(proposal)
        except ApprovalError as error:
            return failure(error.code, str(error)), None
        # Interactive callers may use ApprovalManager.prompt(), while automated
        # tests can return the literal yes/no/cancel decision.
        try:
            outcome = response if hasattr(response, "grant") and hasattr(response, "decision") else self.service.approvals.decide(proposal.proposal_id, response)
        except ApprovalError as error:
            return failure(error.code, str(error)), None
        if outcome.grant is None:
            return failure("APPROVAL_DENIED" if outcome.decision.decision == "reject" else "USER_CANCELLED",
                           "Human did not approve the proposed write"), None
        return self.service.call(name, args, task_id=task_id, proposal_id=proposal.proposal_id,
                                 grant=outcome.grant, context=context), None

    def _verify_successful_write(self, write_args, task_id, controller, transcript):
        """Controller-owned read-back; one independently budgeted tool call."""
        args = {"issue_id": write_args["issue_id"]}
        step = controller.begin_decision()
        controller.before_tool_call("read_issue", args)
        result = self.service.call(
            "read_issue", args, task_id=task_id, context=CallContext(task_id=task_id, step=step),
        )
        transcript.append({"kind": "tool_result", "result": {"tool": "read_issue", "result": result}})
        return result

    @staticmethod
    def _latest_issue(transcript):
        for entry in reversed(transcript):
            result = entry.get("result", {})
            if result.get("tool") == "read_issue" and result.get("result", {}).get("ok"):
                return result["result"]["data"]
        return None

    @staticmethod
    def _writes_verified(transcript):
        successful_writes = [entry["result"] for entry in transcript if entry.get("kind") == "tool_result"
                             and entry["result"].get("tool") in WRITE_TOOLS and entry["result"]["result"].get("ok")]
        if not successful_writes:
            return True
        reads = [entry["result"]["result"].get("data") for entry in transcript if entry.get("kind") == "tool_result"
                 and entry["result"].get("tool") == "read_issue" and entry["result"]["result"].get("ok")]
        if not reads:
            return False
        latest = reads[-1]
        for write in successful_writes:
            data = write["result"]["data"]
            if "labels" in data and not set(data["labels"]).issubset(set(latest.get("labels", []))):
                return False
            if "comment" in data and not any(comment.get("operation_id") == data["comment"]["operation_id"]
                                                for comment in latest.get("comments", [])):
                return False
        return True

    @classmethod
    def _final_is_supported(cls, transcript, scope):
        writes = [entry["result"] for entry in transcript if entry.get("kind") == "tool_result"
                  and entry["result"].get("tool") in WRITE_TOOLS]
        if scope.read_only:
            return not any(entry["result"].get("result", {}).get("ok") for entry in writes) and cls._read_only_completion_supported(
                transcript, require_labels=False,
            )
        if writes:
            # A rejection, cancellation, or validated write failure is a real,
            # tool-backed terminal outcome. A successful write additionally
            # requires the read-back proof below.
            return cls._writes_verified(transcript) and all(
                entry["result"].get("ok")
                or entry["result"].get("error", {}).get("code") != "INVALID_ARGUMENTS"
                for entry in writes
            )
        return cls._read_only_completion_supported(transcript, require_labels=True)

    @staticmethod
    def _read_only_completion_supported(transcript, *, require_labels):
        """Allow a no-op only after investigation proves no classification gap."""
        issue = TriageAgent._latest_issue(transcript)
        labels_result = next((entry["result"]["result"] for entry in reversed(transcript)
                              if entry.get("kind") == "tool_result"
                              and entry["result"].get("tool") == "get_labels"
                              and entry["result"]["result"].get("ok")), None)
        if issue is None:
            return False
        if not require_labels:
            return True
        if labels_result is None:
            return False
        if issue.get("state") != "open":
            return True
        categories = {row["name"]: row["category"] for row in labels_result["data"].get("taxonomy", [])}
        type_count = sum(categories.get(label) == "type" for label in issue.get("labels", []))
        priority_count = sum(categories.get(label) == "priority" for label in issue.get("labels", []))
        return type_count == 1 and priority_count == 1

    @staticmethod
    def _write_investigation_supported(transcript, arguments):
        issue = TriageAgent._latest_issue(transcript)
        has_labels = any(
            entry.get("kind") == "tool_result"
            and entry["result"].get("tool") == "get_labels"
            and entry["result"]["result"].get("ok")
            for entry in transcript
        )
        return issue is not None and has_labels and issue.get("id") == arguments.get("issue_id")

    @staticmethod
    def _call_is_in_scope(name, arguments, scope):
        if name == "read_issue":
            return arguments.get("issue_id") == scope.issue_id
        if name in WRITE_TOOLS:
            return scope.permits_writes and arguments.get("issue_id") == scope.issue_id
        return name == "get_labels"

    def _retry(self, name, args, task_id, controller, transcript, approval_handler, proposal_presenter):
        # A retry consumes a new decision and the service's idempotency ledger protects writes.
        step = controller.begin_decision()
        controller.before_tool_call(name, args, retry=True)
        result, pending = self._call(name, args, task_id, step, approval_handler,
                                     issue_context=self._latest_issue(transcript),
                                     proposal_presenter=proposal_presenter)
        transcript.append({"kind": "tool_result", "result": {"tool": name, "result": result}})
        return result
