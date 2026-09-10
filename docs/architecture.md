# Architecture

## Goal

Local-MCP-Bridge provides a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources.

The bridge must never treat the AI model itself as a trusted security principal. Authorization is enforced locally by deterministic code.

## High-level architecture

```text
MCP client / AI agent
        |
        | authenticated MCP transport
        v
+---------------------------+
| MCP tool layer            |
| - list/read/search         |
| - git operations          |
| - job operations          |
+-------------+-------------+
              |
              v
+---------------------------+
| Policy / security layer   |
| - project registry        |
| - path confinement        |
| - executable allowlists   |
| - permission checks       |
| - limits/timeouts         |
+-------------+-------------+
              |
              v
+---------------------------+
| Local execution layer     |
| - pathlib/filesystem      |
| - subprocess/job manager  |
| - Git                     |
+-------------+-------------+
              |
              v
       Authorized projects
```

## Planned modules

```text
src/local_mcp_bridge/
├── server.py
├── config.py
├── models.py
├── tools/
│   ├── filesystem.py
│   ├── git.py
│   └── jobs.py
├── security/
│   ├── paths.py
│   ├── commands.py
│   └── permissions.py
└── runtime/
    ├── job_manager.py
    └── audit.py
```

## Capability design

Prefer narrow MCP tools such as:

```text
list_directory(project, path)
read_file(project, path)
search_text(project, query, path)
git_status(project)
git_pull(project)
start_job(project, executable, args)
get_job_status(job_id)
read_job_output(job_id)
cancel_job(job_id)
```

Avoid a generic interface such as:

```text
shell(command)
```

because it collapses the entire security model into unrestricted command execution.

## Job model

Long-running commands should execute as managed local jobs instead of holding one MCP request open until completion.

```text
start_job(...)
    -> job_id

get_job_status(job_id)
    -> queued | running | succeeded | failed | cancelled

read_job_output(job_id)
    -> bounded stdout/stderr or structured artifacts
```

This allows benchmark and test processes to survive an interrupted AI interaction while keeping process state auditable.

## Configuration boundary

The repository contains only templates. Machine-specific paths and permissions live in ignored local configuration.

```text
config/config.example.yaml   tracked
config/config.yaml           local only
.env.example                 tracked
.env                         local only
```

## Transport boundary

Initial development should bind only to loopback (`127.0.0.1`). Remote/browser-based MCP clients should connect through an authenticated encrypted tunnel or equivalent secure transport. Direct unauthenticated public exposure is out of scope.
