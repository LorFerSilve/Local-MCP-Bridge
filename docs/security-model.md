# Security Model

## Trust model

The bridge assumes all of the following may be malicious, compromised, misleading, or simply wrong:

- model-generated tool calls;
- repository text, logs, documentation, generated artifacts, and test fixtures;
- MCP client input;
- filenames, paths, and search queries;
- future command arguments;
- future child-process output.

The deterministic local policy layer is the authorization boundary. Security must not depend on the model following instructions correctly.

## Deny by default

Every project is registered explicitly. Capabilities are granted per project and per operation.

Current capability flags are:

- `read` — list directories and read text files;
- `search` — perform bounded plain-text search, requiring `read` as well;
- `execute` — reserved for a later phase;
- `git` — reserved for a later phase.

A missing permission means denied.

## Project registry guarantees

The registry maps AI-visible logical project IDs to canonical local filesystem roots while keeping those roots server-side.

Before a project enters the registry:

1. its ID must match the restricted lowercase grammar;
2. its root must be absolute;
3. the root must exist and resolve successfully;
4. the resolved root must be a directory;
5. registered roots may not be identical or overlap/nest;
6. duplicate YAML mapping keys are rejected;
7. unknown project and permission keys are rejected;
8. permissions default to denied;
9. `search: true` requires `read: true`.

No local config means an empty registry. An explicitly selected missing or invalid config fails closed.

## Phase 3 filesystem authorization

Phase 3 introduces read-only filesystem content access. Each operation follows this authorization sequence:

1. resolve the logical project ID;
2. verify the operation-specific permission;
3. parse the caller path as project-relative input;
4. reject dangerous lexical forms;
5. reject common sensitive/credential paths;
6. reject ordinary symbolic-link components;
7. strictly resolve the effective filesystem path;
8. verify the resolved path remains inside the canonical project root;
9. apply type checks and hard resource limits;
10. only then return bounded content or metadata.

Caller-supplied path fields never select a host root.

### Rejected path forms

Phase 3 rejects, among other cases:

- POSIX absolute paths;
- Windows drive-qualified and UNC paths;
- `..` parent traversal;
- NUL/control characters;
- NTFS alternate-data-stream syntax using `:`;
- Windows reserved device names such as `CON`, `NUL`, `COM1`, and `LPT1`;
- path components ending in a Windows-ambiguous space or period;
- ordinary symbolic-link components.

The implementation verifies containment using resolved path objects rather than string-prefix checks.

## Sensitive-path defense in depth

Read permission does not automatically expose every file under the root. Phase 3 also denies common secret-bearing locations and formats, including examples such as:

- `.env` and non-template `.env.*` files;
- `.git` and common SSH/cloud credential directories;
- `credentials`, `secrets`, and `tokens` directories;
- `.netrc`, `.npmrc`, `.pypirc`, and Git credential files;
- common private-key/container formats such as `.key`, `.pem`, `.p12`, `.pfx`, and `.ppk`;
- Terraform state and common service-account credential files.

Environment templates such as `.env.example`, `.env.sample`, and `.env.template` remain readable.

This filter is defense in depth. It cannot identify every possible secret, so operators should still keep secrets outside authorized project roots whenever practical.

## Bounded reads and search

Filesystem content is treated as untrusted data and resource usage is capped.

`read_file`:

- accepts UTF-8 text only;
- rejects NUL-containing/binary content;
- enforces a hard file-size ceiling;
- reads at most `limit + 1` bytes from the file descriptor;
- returns at most a bounded number of lines per call;
- exposes `next_start_line` for continuation.

`list_directory`:

- inspects only a bounded number of entries;
- omits sensitive, symbolic-link, and unsupported entries;
- returns project-relative paths only.

`search_text`:

- performs literal substring search rather than arbitrary regex evaluation;
- bounds query length and result count;
- bounds total directory entries, files, per-file bytes, and total bytes scanned;
- bounds directory enumeration before materializing entries in memory;
- skips binary, oversized, unreadable, restricted, and escaping paths.

## Remaining filesystem hardening

Phase 3 does not claim that application-level `resolve`/check/open sequences are a complete hostile-filesystem sandbox. Phase 4 must specifically address:

- Windows junctions and other reparse points;
- filesystem identity changes between authorization and use (TOCTOU);
- race-resistant open strategies where supported;
- adversarial Windows fixtures;
- mount/reparse semantics that differ across platforms.

Process execution remains disabled until this work is completed.

## Process execution

Future execution must use an executable plus an argument vector, not a shell command string.

Preferred conceptual API:

```text
start_job(
    project_id="example-project",
    executable="python",
    args=["scripts/benchmark.py", "--phase", "10"]
)
```

Controls should include executable allowlists, project-scoped working directories, filtered environment inheritance, timeouts, output caps, concurrency limits, cancellation, job metadata, and local audit records.

## Git operations

Git should be exposed as narrow operations rather than arbitrary Git command strings where practical. Force-push, reset, clean, branch deletion, and history rewriting remain denied unless a separately reviewed policy explicitly introduces them.

## Output handling

Tool output is untrusted data. Repository text can contain prompt injection instructions, terminal control characters, secrets, or very large content.

The bridge therefore bounds content size and sanitizes control characters in search previews. Full source reads preserve text fidelity because code may legitimately contain escapes; clients must still treat returned content as data rather than authority.

## Network exposure

Development currently uses local stdio transport. Remote access will require authenticated encrypted transport, preferably through a controlled tunnel or reverse proxy.

Authentication complements but never replaces local capability checks.

## Auditability

A later phase will add local audit records for security-relevant operations. Logs must remain local and must not themselves become a secret-exfiltration channel.

## Future write access

Filesystem modification is intentionally separate from read access. Before write/delete/rename tools are introduced, the project must add transactional safeguards, path-confinement tests, overwrite policy, recovery considerations, and explicit destructive-operation policy.
