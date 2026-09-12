# Phase 8 — Audit logging and runtime hardening

## Goal

Phase 8 adds a local operational audit trail without expanding the MCP client's authority.
The audit layer records **what class of operation occurred and whether it succeeded**, but
it deliberately does not become a second data-exfiltration surface.

The configured runtime enables persistent audit logging by default. The reusable
`create_mcp_server(...)` factory remains hermetic and non-persistent unless a logger is
explicitly injected.

## Audit location

The configured runtime writes audit state beneath:

```text
runtime/audit/
```

The directory is already covered by the repository's `runtime/` ignore policy. An operator
may choose another location with:

```text
LOCAL_MCP_BRIDGE_AUDIT_DIR
```

An explicit override must be an absolute path. The audit directory is local runtime state
and must never be committed.

## Event format

Audit records are newline-delimited JSON (`audit.jsonl`). Each event contains only a fixed,
bounded schema:

```json
{
  "schema_version": 1,
  "timestamp": "<UTC timestamp>",
  "session_id": "<opaque random id>",
  "sequence": 1,
  "event_id": "<opaque random id>",
  "action": "filesystem.read_file",
  "outcome": "success",
  "project_id": "example-project",
  "details": {
    "requested_max_lines": 400
  }
}
```

`session_id` separates bridge process lifetimes. `sequence` is monotonic only within that
session; it is not presented as a global durable counter.

## Fixed metadata schema

The logger intentionally has **no schema field** for:

- raw process/job argv;
- process stdout or stderr;
- filesystem paths supplied by the caller;
- host absolute paths;
- search queries;
- file contents;
- executable targets or executable paths;
- Git remote URLs;
- fetched object contents;
- credentials, tokens, environment variables, headers, or authentication material;
- arbitrary exception/error text.

Detail keys are allowlisted. Values are limited to bounded integers, booleans, `null`, and
a few fixed enums such as job status, output stream name, and termination reason. Project
IDs must satisfy the same registry identifier policy used by the authorization layer.

This makes accidental secret logging materially harder than a generic structured logger
that accepts arbitrary dictionaries or exception strings.

## Audited operations

Phase 8 records security-relevant MCP activity including:

- directory listing;
- file reads;
- text search;
- synchronous process execution;
- managed-job start, lookup, listing, output access, and cancellation;
- Git status;
- Git fetch;
- Git fast-forward synchronization;
- configured runtime bootstrap.

Routine health/project-metadata calls remain deliberately low-noise and do not create
additional audit records.

## Fail-closed high-impact operations

The configured runtime treats audit availability as part of authorization for operations
that can execute code, mutate process state, access the network through Git, or modify an
authorized working tree.

Before the effect occurs, these operations require a successful `attempt` audit write:

```text
run_process
start_job
cancel_job
git_fetch
git_sync_fast_forward
```

If that pre-operation write fails, the operation is refused.

Read-only filesystem/Git-status/job-inspection calls do not fail merely because an audit
completion write fails. The logger instead becomes unhealthy. `health_check()` then reports
`status="degraded"` and `audit_healthy=false`.

Completion logging after a high-impact effect is intentionally non-transactional: once an
OS process has run or a Git update has happened, returning an artificial tool error cannot
undo the effect. A failed completion write marks audit unhealthy, and the **next**
high-impact attempt fails closed unless audit persistence has recovered.

## Hardened audit path

Audit state is treated as untrusted local filesystem input. Phase 8 therefore:

1. walks/creates the audit directory component-by-component;
2. rejects symlink/name-surrogate redirecting components and non-directories;
3. uses private POSIX directory/file modes (`0700` / `0600`) where meaningful;
4. rejects redirecting, non-regular, and hard-linked audit files;
5. uses exclusive creation when an active log does not already exist;
6. uses `O_NOFOLLOW` where the platform exposes it;
7. compares file identity around authorization/open for existing files;
8. writes one bounded JSONL event and calls `fsync` before reporting success.

A hostile local actor with equivalent OS privileges can still race filesystem state around
application-level checks. Phase 8 does not claim kernel-enforced isolation from such an
actor.

## Bounded disk usage

The active audit log defaults to a 4 MiB ceiling and rotates before the next event would
cross that boundary. At most five audit files are retained by default, including the active
file.

Hard ceilings are enforced in code:

- maximum active-file size: 64 MiB;
- maximum retained file count: 16;
- maximum encoded event size: 4096 bytes;
- maximum metadata fields per event: 16.

Rotation also validates existing archive destinations before replacing them. Unsafe local
objects make rotation fail closed rather than being silently followed.

## MCP exposure

There is **no MCP tool for reading raw audit logs**. Audit files are operational local state,
not model context. This prevents the log from becoming a repository-controlled prompt-
injection channel or a convenient path for replaying sensitive operational metadata to a
remote model.

Only two non-sensitive booleans are exposed through `health_check()`:

```text
audit_enabled
audit_healthy
```

When an enabled logger becomes unhealthy, the overall health status changes from `ok` to
`degraded`.

## Runtime bootstrap

The configured runtime order is:

```text
load base project policy
        |
        v
apply optional Git policy
        |
        v
prepare hardened audit directory/logger
        |
        v
persist runtime.bootstrap attempt
        |
        v
create execution service + persistent JobManager
        |
        v
create MCP server with injected AuditLogger
        |
        v
persist runtime.bootstrap success
```

An invalid explicit audit-directory override or an unsafe audit path aborts configured
runtime startup rather than silently disabling auditing.

## Test/runtime isolation

The pure server factory constructs `AuditLogger()` with no directory when no logger is
injected. That logger performs no filesystem I/O and returns `audit_enabled=false`.

This preserves the existing hermetic boundary:

```text
pytest/server import -> no machine-local config, jobs, or audit state
configured runtime   -> local config + Git overlay + jobs + persistent audit state
```

## Residual risks

Phase 8 is an application-level audit and hardening layer, not tamper-proof forensic
logging. In particular:

- a local administrator or same-privilege actor can delete or alter audit files;
- no remote log sink or cryptographic signing key is introduced;
- log rotation is bounded retention, not archival retention;
- abrupt power loss can still affect filesystem durability despite per-event `fsync`;
- a completion event can fail after an underlying effect already occurred;
- audit records intentionally omit detailed request data, so they are useful for operational
  accountability but are not a full replay trace.

Remote/tunnel exposure remains Phase 9 and must preserve these local authorization and
audit boundaries rather than bypassing them.
