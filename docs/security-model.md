# Security Model

## Trust model

The bridge assumes that all of the following may be malicious, compromised, or simply wrong:

- model-generated tool calls;
- text found inside source repositories, logs, documentation, benchmark output, and test fixtures;
- MCP client input;
- filenames and paths supplied by callers;
- command arguments;
- child-process output.

The local policy layer is the trust boundary. Security decisions must not depend on the model following instructions correctly.

## Deny by default

Every project is registered explicitly. Capabilities are granted per project and per operation.

Example capability classes:

- read/list/search;
- process execution;
- Git synchronization;
- future write/modify operations.

A missing permission means denied.

## Filesystem confinement

Every requested path must be interpreted relative to a registered project root unless a future API explicitly states otherwise.

Before access:

1. identify the selected project root;
2. normalize and canonicalize the candidate path;
3. verify the resolved path remains within the canonical project root;
4. detect escape through symlinks, Windows junctions, mount points, or reparse points as appropriate;
5. apply operation-specific permissions and size limits;
6. only then perform I/O.

String-prefix checks such as `candidate.startswith(root)` are insufficient and must not be used as the sole confinement check.

## Process execution

The default execution model must use an executable plus an argument vector, not a single shell command string.

Preferred conceptual API:

```text
start_job(
    project="example",
    executable="python",
    args=["scripts/benchmark.py", "--phase", "10"]
)
```

The implementation should launch processes with shell interpretation disabled unless a future explicitly approved capability requires otherwise.

Controls should include:

- executable allowlist;
- project-scoped working directory;
- bounded environment inheritance;
- timeout;
- output-size cap;
- concurrency cap;
- cancellation;
- persistent job metadata;
- local audit record.

## Git operations

Git should be exposed as narrow operations instead of arbitrary Git command strings where practical. Destructive operations such as force-push, reset, clean, branch deletion, or history rewriting should be denied by default and require a separately reviewed policy if ever added.

## Secrets

The bridge must not provide an MCP tool that dumps the host environment or secret stores. Environment variables passed to child processes should be minimized and filtered where possible.

Secrets must never be returned in normal tool output or written to public audit logs.

## Output handling

Tool and process output is untrusted data. It may contain prompt injection instructions, terminal escape sequences, secrets, or extremely large content.

The bridge should:

- bound output sizes;
- prefer plain/structured output;
- avoid interpreting terminal control sequences;
- allow tail/range reads rather than returning unbounded logs;
- redact configured secret patterns where feasible.

## Network exposure

Development mode should bind to loopback only. Remote access requires authenticated encrypted transport, preferably through a controlled tunnel or reverse proxy.

Authentication and transport security complement but do not replace local capability checks.

## Auditability

Security-relevant actions should record, at minimum:

- timestamp;
- project identifier;
- requested capability;
- executable/operation name;
- sanitized arguments or argument metadata;
- result state and exit code;
- job identifier when applicable.

Audit logs remain local and are ignored by Git.

## Future write access

Filesystem modification is intentionally separate from read access. Before write/delete/rename tools are introduced, the project should add transactional safeguards, path confinement tests, overwrite policy, backup/recovery considerations, and explicit confirmation/policy for destructive operations.
