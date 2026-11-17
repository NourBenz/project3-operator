# The Operator issue console

From the repository root, start the dashboard with the same Python environment used for the CLI:

```bash
.venv/bin/python -m web
```

Open http://127.0.0.1:8765. The default mode seeds a temporary sandbox. Select an open issue, click Run agent, and review the proposed update. Approving commits to the sandbox and refreshes the issue. The sandbox resets when the server stops.

To use the configured GitHub repository:

```bash
.venv/bin/python -m web --backend github
```

The dashboard uses the existing server-side `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and OpenAI settings. You can select a repository at startup with `--repository OWNER/REPO`. In GitHub mode, the agent uses OpenAI to triage the issue. Review the exact proposal before approving any label or comment update. To run triage alone with the deterministic model, add `--offline`.

The dashboard listens on loopback for local use. Run one workflow at a time. Stop the server with Ctrl+C.

## LangGraph and LangSmith

LangGraph runs the same triage workflow for the dashboard and CLI. The approval handlers decide
whether a proposed label or comment update is authorized.

To opt in to workflow traces, add these settings to your local `.env` and
restart the dashboard or rerun the CLI:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_key_here
LANGSMITH_PROJECT=project3-operator
```

LangSmith receives workflow steps, timings, and status codes. Issue content,
model prompts and approval grants stay outside the traced graph
state. Tracing is off by default.
