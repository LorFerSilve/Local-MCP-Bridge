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
| - safe project metadata   |
| - future filesystem tools |
| - future job/Git tools    |
+-------------+-------------+
              |
              v
+---------------------------+
| Policy / security layer   |
| - project registry        |
| - canonical allowed roots |
| - permission checks       |
| - future path confinement |
| - future exec allowlists  |
+-------------+-------------+
              |
              v
+---------------------------+
| Local host layer          |
| - pathlib/filesystem      |
| - future processes / Git  |
+-------------+-------------+
              |
              v
       Authorized projects
```

## Active Phase 2 modules

```text
src/local_mcp_bridge/
├── __init__.py
├── __main__.py
├── config.py
├── registry.py
└── server.py
```

`config.py` parses the ignored local YAML configuration, rejects ambiguous or unsafe registry definitions, resolves authorized roots, and constructs an immutable `ProjectRegistry`.

`registry.py` owns the internal mapping from a logical project ID to its canonical absolute root and capability flags. Its public representations intentionally omit the root path.

`server.py` exposes only MCP-safe metadata in Phase 2: health, project listing, and project lookup.

## Project identity boundary

MCP callers should address projects by stable logical IDs rather than host paths:

```text
AI-visible:
    project = "aurum"

server-internal:
    "aurum" -> C:/.../aurum-forecasting-tool
```

The second mapping is local configuration and must never be returned by normal MCP metadata tools.

This separation is foundational for later APIs such as:

```text
list_directory(project="aurum", path=".")
read_file(project="aurum", path="README.md")
start_job(project="aurum", executable="python", args=[...])
```

The caller selects an authorized project plus a project-relative path; it does not supply an arbitrary host filesystem root.

## Configuration boundary

The repository contains only templates. Machine-specific paths and permissions live in ignored local configuration.

```text
config/config.example.yaml   tracked
config/config.yaml           local only
.env.example                 tracked
.env                         local only
```

Phase 2 configuration loading is fail-closed:

- no local config means an empty project registry;
- an explicitly selected but missing/invalid config is an error;
- duplicate YAML keys are rejected;
- roots must be absolute, existing directories;
- canonical roots must be unique;
- permissions default to denied;
- public project metadata does not contain the root.

## Planned capability design

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

## Transport boundary

Initial development uses MCP stdio locally. Remote/browser-based MCP clients should later connect through an authenticated encrypted tunnel or equivalent secure transport. Direct unauthenticated public exposure is out of scope.
