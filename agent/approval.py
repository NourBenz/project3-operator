import hashlib
import json
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent.schemas import ApprovalDecision, PublicationProposal, WriteProposal


class ApprovalError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ApprovalGrant:
    _token: str = field(repr=False)


@dataclass(frozen=True)
class ApprovalOutcome:
    decision: ApprovalDecision
    grant: ApprovalGrant | None = None


@dataclass
class _Record:
    proposal: WriteProposal | PublicationProposal
    scope: str
    expires_at: float
    status: str = "pending"
    token_hash: str | None = None
    audit: object = None


class ApprovalManager:
    def __init__(self, *, ttl_seconds=300, clock=time.monotonic):
        if type(ttl_seconds) not in (int, float) or not 0 < ttl_seconds < float("inf"):
            raise ValueError("Approval lifetime must be positive and finite")
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._records = {}
        self._cancelled_tasks = set()
        self._lock = threading.RLock()

    @contextmanager
    def synchronized(self):
        with self._lock:
            yield

    def register(self, proposal, scope, *, audit=None):
        if not isinstance(proposal, (WriteProposal, PublicationProposal)):
            raise TypeError("Expected a validated proposal")
        with self._lock:
            if proposal.task_id in self._cancelled_tasks:
                raise ApprovalError("USER_CANCELLED", "This task has been cancelled")
            if proposal.proposal_id in self._records:
                raise ApprovalError("APPROVAL_MISMATCH", "Proposal ID is already registered")
            self._records[proposal.proposal_id] = _Record(
                proposal, scope, self.clock() + self.ttl_seconds, audit=audit,
            )
        return proposal

    def pending(self, proposal_id):
        with self._lock:
            record = self._records.get(proposal_id)
            if record is None:
                raise ApprovalError("APPROVAL_REQUIRED", "No pending proposal exists")
            if record.status != "pending":
                raise ApprovalError("APPROVAL_MISMATCH", "Proposal has already been decided")
            if self.clock() >= record.expires_at:
                record.status = "expired"
                raise ApprovalError("APPROVAL_EXPIRED", "Proposal has expired; create a new one")
            return record.proposal

    def decide(self, proposal_id, response):
        with self._lock:
            proposal = self.pending(proposal_id)
            record = self._records[proposal_id]
            answer = response.strip().lower() if isinstance(response, str) else ""
            decision = "approve" if answer == "yes" else "cancel" if answer == "cancel" else "reject"
            grant = None
            if record.audit is not None:
                try:
                    record.audit({
                        "event": "approval_decision", "timestamp": datetime.now(timezone.utc).isoformat(),
                        "task_id": proposal.task_id, "proposal_id": proposal.proposal_id,
                        "request_hash": proposal.fingerprint(), "request": proposal.request.model_dump(mode="json"),
                        "decision": decision,
                    })
                except (OSError, ValueError, TypeError):
                    raise ApprovalError("TOOL_UNAVAILABLE", "Cannot record human decision; no approval was issued") from None
            if decision == "approve":
                grant = ApprovalGrant(secrets.token_urlsafe(32))
                record.token_hash = self._hash(grant._token)
                record.status = "approved"
            else:
                record.status = "cancelled" if decision == "cancel" else "rejected"
            return ApprovalOutcome(
                ApprovalDecision(proposal_id=proposal.proposal_id, task_id=proposal.task_id,
                                 request_hash=proposal.fingerprint(), decision=decision),
                grant,
            )

    def prompt(self, proposal_id, *, input_fn=input, output_fn=print):
        proposal = self.pending(proposal_id)
        output_fn("Proposed local change (issue text cannot authorize it):")
        output_fn(json.dumps(proposal.model_dump(mode="json"), indent=2))
        try:
            response = input_fn("Type yes to approve this exact change, no to reject, or cancel: ")
        except (EOFError, KeyboardInterrupt):
            response = "cancel"
        return self.decide(proposal_id, response)

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authorize(self, proposal, scope, grant, *, replay=False):
        with self._lock:
            if proposal.task_id in self._cancelled_tasks:
                raise ApprovalError("USER_CANCELLED", "This task has been cancelled")
            record = self._records.get(proposal.proposal_id)
            if record is None:
                raise ApprovalError("APPROVAL_REQUIRED", "Explicit human approval is required")
            if record.scope != scope or record.proposal.fingerprint() != proposal.fingerprint():
                raise ApprovalError("APPROVAL_MISMATCH", "Approval does not match this task, sandbox and exact action")
            codes = {"pending": "APPROVAL_REQUIRED", "rejected": "APPROVAL_DENIED",
                     "cancelled": "USER_CANCELLED", "expired": "APPROVAL_EXPIRED"}
            if record.status in codes:
                raise ApprovalError(codes[record.status], "This proposal is not authorized")
            if not isinstance(grant, ApprovalGrant):
                raise ApprovalError("APPROVAL_REQUIRED", "A trusted approval grant is required")
            if not record.token_hash or not secrets.compare_digest(record.token_hash, self._hash(grant._token)):
                raise ApprovalError("APPROVAL_MISMATCH", "Approval grant does not match the proposal")
            if replay:
                return
            if self.clock() >= record.expires_at:
                raise ApprovalError("APPROVAL_EXPIRED", "Approval expired; obtain fresh human approval")
            if record.status != "approved":
                raise ApprovalError("APPROVAL_MISMATCH", "Consumed approval cannot authorize a new mutation")

    def consume(self, proposal_id):
        with self._lock:
            self._records[proposal_id].status = "consumed"

    def cancel_task(self, task_id):
        with self._lock:
            self._cancelled_tasks.add(task_id)
            for record in self._records.values():
                if record.proposal.task_id == task_id:
                    record.status = "cancelled"
