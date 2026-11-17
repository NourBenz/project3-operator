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

The dashboard uses the existing server-side `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and OpenAI settings. You can select a repository at startup with `--repository OWNER/REPO`. In GitHub mode, one run triages the issue, then investigates and tests a fix after triage succeeds. The agent uses OpenAI and the existing Docker test runner. Review the exact proposal before each GitHub write, including draft PR publication. To run triage alone with the deterministic model, add `--offline`.

The dashboard listens on loopback for local use. Run one workflow at a time. Stop the server with Ctrl+C.
