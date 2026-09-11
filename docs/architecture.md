# Architecture

## Goal

Local-MCP-Bridge provides a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources.

The AI model is never treated as a trusted security principal. Authorization and resource limits are enforced locally by deterministic code.

## High-level architecture

```text
MCP client / AI agent
        |
        | MCP tool calls
        v
+---------------------------+
| MCP tool layer            |
| - project metadata        |
| - list_directory          |
| - read_file               |
| - search_text             |
| - future job/Git tools    |
+-------------+-------------+
              |
              v
+---------------------------+
| Policy / security layer   |
| - project registry        |
| - read/search permissions |
| - relative-path policy    |
| - canonical containment   |
| - sensitive-path filter   |
| - bounded I/O/search      |
+-------------+-------------+
              |
              v
+---------------------------+
| Local host layer          |
| - pathlib / os.scandir    |
| - bounded file reads      |
| - future processes / Git  |
+-------------+-------------+
              |
              v
       Authorized projects
```

## Active Phase 3 modules

```text
src/local_mcp_bridge/
├── __init__.py
├── __main__.py
├── config.py
├── registry.py
├── server.py
└── tools/
    ├── __init__.py
    └── filesystem.py
```

`config.py` parses ignored local YAML configuration, validates project definitions, canonicalizes authorized roots, and constructs an immutable `ProjectRegistry`.

`registry.py` maps logical project IDs to canonical local roots and capability flags. Public project metadata intentionally omits host paths.

`tools/filesystem.py` implements the Phase 3 read-only policy: relative-path validation, common secret-path denial, standard symlink rejection, canonical root containment, bounded UTF-8 reads, bounded directory listing, and bounded plain-text recursive search.

`server.py` binds those deterministic capabilities to MCP tools. It does not perform filesystem authorization itself.

## Project identity boundary

MCP callers address projects by logical IDs rather than host paths:

```text
AI-visible:
    project_id = "example-project"

server-internal:
    "example-project" -> C:/absolute/local/project/root
```

The mapping stays in ignored local configuration and is not returned by project metadata or filesystem path fields.

Phase 3 calls therefore look like:

```text
list_directory(project_id="example-project", path="src")
read_file(project_id="example-project", path="src/main.py")
search_text(project_id="example-project", query="TODO", path="src")
```

A caller cannot choose an arbitrary host root for an individual operation.

## Read-only filesystem flow

```text
MCP request
    |
    v
resolve project_id
    |
    v
check read/search permission
    |
    v
validate project-relative path
    |
    +-- reject absolute / UNC / drive / .. / ADS / device path
    |
    v
apply sensitive-path policy
    |
    v
reject standard symlink components
    |
    v
strictly resolve effective path
    |
    v
verify effective path remains under canonical project root
    |
    v
perform bounded read/list/search
    |
    v
return project-relative metadata/content
```

This is an application-level policy boundary, not an operating-system sandbox.

## Filesystem resource model

Phase 3 uses hard upper bounds rather than trusting caller-supplied limits:

- single-file bytes read;
- lines returned per `read_file` call;
- directory entries inspected per listing;
- recursive search entries and files;
- bytes searched per file and per request;
- query length and number of matches.

File descriptors are read with an explicit byte cap plus one sentinel byte. This prevents a file that grows between `stat()` and `read()` from causing an unbounded allocation.

Recursive search also bounds directory enumeration instead of materializing an arbitrarily large directory before applying limits.

## Sensitive-path policy

The read-only tools deny common secret-bearing locations such as `.env`, `.git`, SSH/cloud credential directories, credential/token directories, private-key formats, and related files. Example/template environment files remain readable.

This is defense in depth, not a substitute for keeping secrets outside authorized project roots.

## Phase 4 boundary

Phase 3 blocks ordinary symbolic-link paths and verifies canonical containment. Phase 4 is intentionally reserved for deeper filesystem hardening, especially:

- Windows junction and reparse-point detection across supported Python versions;
- race/TOCTOU analysis between authorization and file open;
- path identity changes during traversal;
- adversarial filesystem fixtures and Windows-specific tests;
- deciding whether in-root links are always denied or can be safely supported.

Process execution will not be introduced until this boundary is reviewed.

## Configuration boundary

The repository contains only templates. Machine-specific paths and permissions live in ignored local configuration.

```text
config/config.example.yaml   tracked
config/config.yaml           local only
.env.example                 tracked
.env                         local only
```

No local config produces an empty registry. An explicitly selected invalid/missing config fails closed.

## Future capability design

Prefer narrow tools such as:

```text
git_status(project_id)
git_pull(project_id)
start_job(project_id, executable, args)
get_job_status(job_id)
read_job_output(job_id)
cancel_job(job_id)
```

Avoid a generic interface such as:

```text
shell(command)
```

because it collapses the security model into unrestricted command execution.

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

## Transport boundary

Initial development uses MCP stdio locally. Browser/cloud MCP clients should later connect only through authenticated encrypted transport. Direct unauthenticated public exposure is out of scope.
