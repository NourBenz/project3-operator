# Architecture

## 1. Names and trust boundaries

Do not confuse these components:

- **Private repository named `MockGitHub`:** a real, private team repository and future Level 2 target.
- **Python `MockGitHub` class:** a local SQLite simulation in `sandbox/mock_github.py`.
- **`GitHubService`:** the API adapter capable of talking to real repositories, including eventually the private repository.

```mermaid
flowchart LR
    A[Private GitHub repository<br/>named MockGitHub]:::real
    B[GitHubService<br/>real API adapter]:::service
    C[Python MockGitHub class<br/>SQLite simulation]:::local
    B -->|GitHub REST API| A
    classDef real fill:#ffe1e1,stroke:#a33
    classDef service fill:#e1edff,stroke:#357
    classDef local fill:#e7ffe1,stroke:#375
```

The private repository was not accessed during this audit.

## 2. Implemented triage architecture

```mermaid
flowchart TD
    U[User / terminal] --> CLI[agent/main.py]
    CLI --> M{Model adapter}
    M -->|offline| FM[FakeModel]
    M -->|configured API| OM[OpenAIModel]
    CLI --> A[TriageAgent]
    A --> TS[Task scope parser]
    A --> SC[StopController]
    A --> R[ToolRegistry]
    R -->|JSON schemas| M
    M -->|one ModelDecision| A
    A -->|validated read call| S[IssueService]
    A -->|write proposal| AP[ApprovalManager]
    AP --> H[Human reviewer]
    H -->|yes / no / cancel| AP
    AP -->|trusted grant on yes| A
    A -->|proposal ID + grant| S
    S --> L[Local MockGitHub + SQLite]
    S --> G[GitHubService]
    L -->|ToolResult| A
    G -->|ToolResult| A
    A -->|controller read-back| S
    A --> O[AgentResult]

    note1[Current agent/main.py always constructs Local MockGitHub]
    CLI -.-> note1
```

`IssueService` is an uncommitted runtime-checkable protocol in the current working tree. The diagram shows the intended dependency injection seam; it does not mean the AI CLI can select GitHubService today.

## 3. Model message and decision flow

```mermaid
sequenceDiagram
    participant C as TriageAgent controller
    participant R as ToolRegistry
    participant M as FakeModel/OpenAIModel
    participant B as IssueService backend

    C->>R: openai_tools()
    C->>M: user request + transcript + tool schemas
    M-->>C: one tool call or final text
    C->>R: validate(name, arguments)
    R-->>C: strict Pydantic model
    C->>C: scope/read-before-write/stop checks
    C->>B: call(read tool, validated arguments)
    B-->>C: normalized ToolResult
    C->>C: append trusted controller trace
    C->>M: next decision with updated trace
```

OpenAIModel sends:

1. a system message containing the trusted triage policy;
2. a user message containing the original request;
3. a user message containing the controller trace, explicitly described as containing untrusted tool output;
4. generated function schemas with parallel calls disabled.

The model proposes actions. It does not dispatch tools, create approval grants, or decide whether evidence is sufficient for completion.

## 4. Human approval flow

```mermaid
sequenceDiagram
    participant M as LLM
    participant C as Controller
    participant S as Backend service
    participant A as ApprovalManager
    participant H as Human

    M-->>C: update_labels/post_comment arguments
    C->>C: validate schema, issue scope, prior reads
    C->>S: propose(tool, exact arguments, task_id)
    S->>A: register proposal + backend scope
    A-->>S: WriteProposal
    S-->>C: WriteProposal
    C->>H: show issue, rationale, exact action
    H-->>C: yes / no / cancel
    alt literal yes
        C->>A: decide(proposal_id, yes)
        A-->>C: ApprovalOutcome + private ApprovalGrant
        C->>S: call(tool, args, proposal_id, grant)
        S->>A: authorize(exact proposal, scope, grant)
        A-->>S: authorized
        S->>S: execute mutation and record outcome
        S->>A: consume(proposal_id)
        S-->>C: ToolResult
    else reject
        A-->>C: no grant / reject
        C-->>H: REJECTED; no write
    else cancel or terminal EOF
        A-->>C: no grant / cancel
        C-->>H: CANCELLED; no write
    end
```

The proposal fingerprint covers proposal ID, task ID, tool, and every argument. Scope separately binds it to a database path or repository URL. A forged boolean such as `approved=true` cannot create the private grant object.

## 5. Controller-owned verification

```mermaid
flowchart TD
    W[Backend reports successful write] --> D[Controller reserves another bounded decision]
    D --> R[Controller calls read_issue]
    R --> T[Append read result to transcript]
    T --> V{Verification}
    V -->|labels included| OK[Final answer may be accepted]
    V -->|local comment operation_id found| OK
    V -->|missing or failed read| NO[Final answer rejected/failed]
    V -->|GitHub comment: operation_id is None| BUG[Known incompatibility]
```

Label verification currently checks that labels returned by the write are included in the latest issue. Comment verification searches for the operation ID. That works locally but not for GitHub read-back comments. The future verifier should match the stable GitHub comment ID and approved body, with bounded read retries that never repost.

## 6. Local backend internals

```mermaid
flowchart TD
    C[MockGitHub.call] --> V[Pydantic discriminated request validation]
    V --> A[Approval authorization]
    A --> X[BEGIN IMMEDIATE SQLite transaction]
    X --> O{Operation ID exists?}
    O -->|exact same request| P[Return stored result]
    O -->|different request| E[DUPLICATE_OPERATION]
    O -->|new| G[Check issue state/version/labels]
    G --> M[Apply labels or comment]
    M --> L[Store outcome in operations table]
    L --> K[Commit atomically]
    K --> Q[Consume approval]
    Q --> R[Return ToolResult]
```

SQLite schema relationships:

```mermaid
erDiagram
    ISSUES ||--o{ ISSUE_LABELS : has
    LABELS ||--o{ ISSUE_LABELS : classifies
    ISSUES ||--o{ COMMENTS : has
    OPERATIONS {
        string operation_id PK
        string action
        string request_hash
        json outcome
        string status
    }
    ISSUES {
        int id PK
        string title
        string body
        string state
        int version
        string source_url
        string source_kind
    }
    LABELS {
        string name PK
        string category
        string description
    }
    COMMENTS {
        int id PK
        int issue_id FK
        string body
        string operation_id UK
        datetime created_at
    }
```

## 7. GitHubService internals

```mermaid
flowchart TD
    C[GitHubService.call] --> A[Approval authorization]
    A --> L{Local operation ledger hit?}
    L -->|exact request| SR[Return saved result]
    L -->|collision| DE[DUPLICATE_OPERATION]
    L -->|new| RI[GET current GitHub issue]
    RI --> SV{state and version valid?}
    SV -->|no| ER[ISSUE_CLOSED / STALE_VERSION]
    SV -->|yes| RW[PUT labels or POST comment]
    RW --> RR[GET updated issue]
    RR --> IL[Insert result in local ledger]
    IL --> CA[Consume approval]
    CA --> N[Send SMTP notification]
    N -->|success| OK[Return success]
    N -->|failure after mutation| PF[Return PARTIAL_FAILURE]

    GAP[Remote mutation and local ledger are not atomic]
    RW -. crash window .-> GAP
```

The ledger protects known exact retries only after it has recorded the remote result. A network/process failure between GitHub accepting a mutation and the ledger insert creates an uncertain outcome that must be reconciled remotely.

## 8. Stop and error architecture

```mermaid
flowchart LR
    D[begin_decision] --> MS{steps < 10?}
    MS -->|no| X[MAX_STEPS]
    MS -->|yes| T{within deadline?}
    T -->|no| TO[TIMEOUT]
    T -->|yes| C[before_tool_call]
    C --> RP{same call repeated > 2?}
    RP -->|yes| LD[LOOP_DETECTED]
    RP -->|no| RT{retry allowance?}
    RT -->|no| RL[RETRY_LIMIT]
    RT -->|yes| E[Execute]
```

Tool errors use `{ok, data, error}`. The logger normalizes Pydantic, approval, timeout, SQLite, and unexpected exceptions into safe responses and emits start/result events with task, step, correlation ID, arguments, duration, and redacted result.

## 9. Offline evaluation architecture

```mermaid
flowchart TD
    J[scenarios.json + injection_tests.json] --> R[eval/runner.py]
    R --> TD[Temporary directory per scenario]
    TD --> SD[Seed temporary SQLite DB]
    SD --> F[FakeModel + MockGitHub/FlakyReadService]
    F --> A[TriageAgent run]
    A --> REC[Trajectory record]
    REC --> MET[eval/metrics.py]
    MET --> OUT[success, trajectory, recovery, injection, cost, latency]
```

Current caveat: scenario expected statuses were not updated after reject/cancel became explicit terminal statuses. A fresh run scores seven safe rejections as failures. Fix the evaluator before using its headline success or injection metrics.

## 10. Target Level 2 architecture

```mermaid
flowchart TD
    U[User selects repository + issue] --> IC[Issue controller]
    IC --> GIS[GitHub issue service]
    GIS --> PRV[Private repository named MockGitHub]
    IC --> WS[Task-specific isolated workspace]
    WS --> INV[Bounded inventory/search/read tools]
    INV --> PLN[Evidence-backed diagnosis and plan]
    PLN --> EDT[Structured patch application]
    EDT --> TST[Sandboxed allowlisted test runner]
    TST --> EV[Patch + test evidence]
    EV --> HA[Human approval bound to repo/base/tree/tests/actions]
    HA -->|reject/cancel| END[Stop without remote code write]
    HA -->|approve| BR[Create non-default branch]
    BR --> CM[Commit approved tree]
    CM --> PS[Push exact branch ref]
    PS --> DPR[Create draft PR]
    DPR --> VR[Read back and verify draft/base/head/SHA/files]
    VR --> DONE[Final verified result]

    POL[Controller policy:<br/>never default-branch push<br/>no merge tool<br/>least privilege]
    POL -.enforces.-> BR
    POL -.enforces.-> PS
    POL -.enforces.-> DPR
```

The issue API service and workspace/git services should remain separate interfaces. The LLM should receive only narrow tools; it should never receive a general shell, raw host path, token, or arbitrary git refspec.
