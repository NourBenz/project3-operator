"""Run the Operator dashboard on loopback with `python -m web`."""
import argparse
import json
import logging
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Condition, Lock, Thread
from urllib.parse import parse_qs, urlsplit

from agent.llm import ModelError, build_model
from agent.loop import TriageAgent
from agent.main import build_backend
from agent.publication import PublicationService
from sandbox.seed import seed_database

STATIC = Path(__file__).with_name("static")


class Run:
    def __init__(self, issue):
        self.id = secrets.token_urlsafe(18)
        self.issue = issue
        self.completed_activity = []
        self.condition = Condition()
        self.state = {"status": "RUNNING", "phase": "Triage", "activity": [], "review": None, "summary": ""}

    def update(self, **changes):
        with self.condition:
            self.state.update(changes)

    def trace(self, transcript):
        activity = []
        for step in transcript:
            result = step.get("result", {})
            tool = step.get("tool") or result.get("tool")
            if tool:
                outcome = result.get("result", result)
                activity.append({"tool": tool, "ok": outcome.get("ok")})
        self.update(activity=self.completed_activity + activity)

    def review(self, manager, proposal, **evidence):
        with self.condition:
            self.answer = None
            self.update(status="AWAITING_APPROVAL", review={
                "id": proposal.proposal_id, "request": proposal.request.model_dump(mode="json"),
                **evidence,
            })
            self.condition.wait_for(lambda: self.answer is not None, timeout=290)
            answer = self.answer or "cancel"
            self.update(status="RUNNING", review=None)
        return manager.decide(proposal.proposal_id, answer)

    def decide(self, proposal_id, answer):
        with self.condition:
            review = self.state["review"]
            if not review or proposal_id != review["id"] or self.answer is not None:
                return False
            self.answer = answer
            self.condition.notify()
            return True


class TracedModel:
    def __init__(self, model, run):
        self.model = model
        self.run = run

    def decide(self, request, transcript, tools):
        self.run.trace(transcript)
        return self.model.decide(request, transcript, tools)

    def decide_with_prompt(self, prompt, request, transcript, tools):
        self.run.trace(transcript)
        return self.model.decide_with_prompt(prompt, request, transcript, tools)


def execute(server, run):
    workspace = None
    try:
        service = server.service
        model = TracedModel(build_model(offline=server.offline), run)
        agent = TriageAgent(model, service=service)
        explanations = {}

        def present(proposal, issue, explanation):
            explanations[proposal.proposal_id] = explanation

        def review(proposal):
            return run.review(service.approvals, proposal,
                              explanation=explanations.pop(proposal.proposal_id, None))

        result = agent.run(
            f"Triage issue {run.issue}", task_id=run.id,
            approval_handler=review, proposal_presenter=present,
        )
        run.trace(result.transcript)
        if result.status != "DONE":
            summary = result.summary
            if not server.offline:
                summary += " Fix investigation did not start because triage did not complete."
            run.update(status=result.status, summary=summary)
            return
        if server.offline:
            run.update(status=result.status, summary=result.summary)
            return

        run.completed_activity = run.state["activity"]
        run.update(phase="Investigating fix", triage_summary=result.summary)
        result = agent.solve(run.issue)
        workspace = result.workspace
        run.trace(result.transcript)
        patch_attempts = [event["result"] for event in result.transcript
                          if event["result"].get("tool") == "propose_patch"]
        evidence = {
            "diff": result.diff,
            "tests": result.tests or (patch_attempts[-1].get("tests", []) if patch_attempts else []),
            "root_cause": result.root_cause,
            "fix_plan": result.fix_plan,
        }
        if any(evidence.values()):
            run.update(evidence=evidence)
        if result.status != "READY_FOR_APPROVAL":
            summary = result.summary
            if not evidence["diff"]:
                summary += " No code fix was generated."
            run.update(status=result.status, summary=summary)
            return
        publisher = PublicationService(service)
        proposal = publisher.prepare(
            task_id=result.task_id, issue=result.issue, workspace=workspace,
            root_cause=result.root_cause, test_evidence=result.tests, limitations=result.limitations,
        )
        run.update(phase="Draft pull request")
        outcome = run.review(publisher.approvals, proposal, **evidence)
        if outcome.decision.decision != "approve":
            run.update(status="CANCELLED", summary="Publication cancelled.", evidence=evidence)
            return
        published = publisher.publish(proposal, outcome.grant, workspace, result.tests)
        run.update(status="DONE", summary="Draft pull request verified.", evidence=evidence,
                   publication=published)
    except ModelError as error:
        run.update(status="FAILED", summary=str(error))
    except Exception as error:
        logging.warning("Dashboard run failed (%s)", type(error).__name__)
        run.update(status="FAILED", summary="The run failed. Check the agent configuration and local logs.")
    finally:
        try:
            if workspace:
                workspace.cleanup()
        finally:
            run.update(review=None, finished=True)


class DashboardHandler(BaseHTTPRequestHandler):
    def allowed_host(self):
        return self.headers.get("Host") == self.server.host

    def respond(self, status, content, mime="application/json; charset=utf-8"):
        body = json.dumps(content).encode() if isinstance(content, dict) else content
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {"error": "Invalid dashboard address."})
        url = urlsplit(self.path)
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/style.css": ("style.css", "text/css")}
        if url.path in assets:
            name, mime = assets[url.path]
            return self.respond(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")
        if url.path == "/api/config":
            return self.respond(200, {"backend": self.server.backend, "offline": self.server.offline,
                                      "scope": self.server.service.scope, "token": self.server.token})
        if url.path == "/api/issues":
            state = parse_qs(url.query).get("state", ["open"])[0]
            result = self.server.service.list_issues(state=state, limit=50)
        elif url.path.startswith("/api/issues/") and url.path.rsplit("/", 1)[1].isdigit():
            result = self.server.service.read_issue(int(url.path.rsplit("/", 1)[1]))
        elif url.path == "/api/run":
            with self.server.lock:
                run = self.server.run
                if not run:
                    return self.respond(200, {"run": None})
                with run.condition:
                    return self.respond(200, {"run": {"id": run.id, "issue": run.issue, **run.state}})
        else:
            return self.respond(404, {"error": "Page not found."})
        if result["ok"]:
            return self.respond(200, result["data"])
        return self.respond(502, {"error": result["error"]["message"]})

    def do_POST(self):
        if (not self.allowed_host() or self.headers.get("Origin") != "http://" + self.server.host
                or self.headers.get("X-Operator-Token") != self.server.token
                or self.headers.get_content_type() != "application/json"):
            return self.respond(403, {"error": "Dashboard request rejected."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            return self.respond(400, {"error": "Invalid request."})
        with self.server.lock:
            run = self.server.run
            if self.path == "/api/run":
                issue = data.get("issue")
                if type(issue) is not int or not 0 < issue <= 2147483647:
                    return self.respond(400, {"error": "Invalid issue number."})
                if run and not run.state.get("finished"):
                    return self.respond(409, {"error": "An agent run is already active."})
                self.server.run = Run(issue)
                Thread(target=execute, args=(self.server, self.server.run), daemon=True).start()
                return self.respond(202, {"id": self.server.run.id})
            if self.path == "/api/decision":
                if (not run or data.get("id") != run.id or data.get("answer") not in ("yes", "no")
                        or not run.decide(data.get("proposal"), data["answer"])):
                    return self.respond(409, {"error": "That proposal is no longer available."})
                return self.respond(202, {"accepted": True})
        return self.respond(404, {"error": "Page not found."})

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("local", "github"), default="local")
    parser.add_argument("--repository")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="operator-web-") as directory:
        db = Path(directory) / "demo.sqlite3"
        if args.backend == "local":
            seed_database(db)
        try:
            service = build_backend(args.backend, db=db, repository=args.repository)
        except ValueError as error:
            parser.error(str(error))
        with ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler) as server:
            server.host = f"127.0.0.1:{server.server_port}"
            server.token = secrets.token_urlsafe(32)
            server.backend = args.backend
            server.offline = args.offline or args.backend == "local"
            server.service = service
            server.lock = Lock()
            server.run = None
            print(f"Operator dashboard at http://{server.host}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
