# Security Policy

Local-MCP-Bridge is security-sensitive software because it exposes controlled local filesystem, process, background-job, and Git synchronization capabilities to AI clients.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest `main` branch unless a release policy is introduced later.

## Reporting a vulnerability

Do **not** publish exploit details, credentials, local paths, tokens, runtime-state contents, or reproducible attacks in a public issue.

When GitHub private vulnerability reporting is enabled for this repository, use a private security advisory/report. If that channel is unavailable, contact the repository owner privately before disclosing technical exploit details.

For non-sensitive hardening suggestions, a normal GitHub issue is appropriate.

## Security invariants

Changes must preserve these invariants unless an explicit security review documents why a broader capability is acceptable:

1. Filesystem access is denied outside configured project roots.
2. Paths are canonicalized and confined before authorization-sensitive use.
3. Symlinks, Windows junctions/redirecting reparse points, hard-linked protected read targets, and traversal components cannot be used to escape read/state policy.
4. Process and managed-job working directories are project-relative and must pass the shared `PathGuard` boundary.
5. A generic shell-command interface is unavailable; known shell executables and Windows shell-script targets are rejected.
6. MCP callers select explicitly configured executable aliases and cannot supply arbitrary host executable paths.
7. Processes are launched with direct argument vectors, disabled stdin, and a minimal environment rather than unrestricted inherited credentials.
8. Read sizes, process arguments, process output, execution time, process concurrency, managed-job inventory, retained history, recovery work, and Git subprocess output/runtime are bounded.
9. Raw managed-job argv is never persisted to durable job state.
10. Persisted job state is treated as untrusted input and must be revalidated before becoming MCP-visible.
11. Recovered jobs are admitted only when their project remains execute-authorized and their executable alias remains allowlisted under the current registry.
12. Recovered output/error text is sanitized and project-root-redacted again; state-file contents do not bypass output policy.
13. The bridge never blindly reattaches to a persisted PID after restart.
14. Authentication secrets, tunnel credentials, local configuration, Git-policy overlays, logs, and runtime job state are never required to be committed.
15. Process output is treated as untrusted data and bounded/sanitized before return or persistence.
16. Remote exposure must use authenticated encrypted transport while retaining local authorization checks.
17. `execute: true` is a high-trust permission and must never be represented as an operating-system sandbox.
18. Git synchronization is enabled only by local operator policy; MCP callers cannot select arbitrary Git URLs, remotes, branches, refspecs, or argv.
19. Phase 7 exposes no push, reset, clean, checkout/switch, rebase, force-push, branch/tag deletion, or history-rewrite primitive.
20. Fast-forward synchronization requires the configured branch, a clean working tree, a verified ancestor relationship, and post-update HEAD verification.
21. Ignored local files may not be silently overwritten by Phase 7 synchronization.
22. Generic execution may not expose Git as a second unrestricted bypass when the Phase 7 runtime policy is active.

## Controlled execution boundary

The bridge exposes structured `run_process(...)` and `start_job(...)` operations rather than `shell(command)`. Both paths ultimately use the same execution policy and allowlist.

Allowlisted programs run under the operating-system account that started Local-MCP-Bridge. A programmable executable such as Python, pytest, a compiler, build system, or package manager may execute project-controlled code that reads or writes host resources, accesses the network, or spawns descendants. Only grant `execute: true` to projects whose code you are willing to execute locally.

For exact executable selection, local-only configuration may pin an alias to an absolute executable path. MCP-visible metadata exposes the alias, not the pinned host path.

## Phase 6 persistent-job boundary

Managed jobs use opaque 128-bit IDs. IDs identify records; they are not the authorization boundary.

Durable state contains bounded metadata and sanitized output, but never raw argv. The default location is `runtime/jobs/`, which is Git-ignored. An operator may override it with an absolute local path through `LOCAL_MCP_BRIDGE_JOB_STATE_DIR`.

State-directory components and state files are checked for unsafe redirection. State-file reads use the same race-resistant `PathGuard` read primitive used elsewhere. Recovery validates schema and field types, current project/executable authorization, sizes, timestamps, status, and output before admitting a record.

Nonterminal recovered records become `interrupted`; the bridge does not persist and blindly reuse a PID. This avoids targeting an unrelated process after PID reuse.

Phase 6 does not claim crash-proof child containment or recursive Windows descendant termination. If the bridge process itself dies, an OS child may survive. Broader runtime isolation remains outside the current application-level boundary.

## Phase 7 Git synchronization boundary

Phase 7 exposes only `git_status`, `git_fetch`, and `git_sync_fast_forward`. The caller supplies only a logical project ID. Remote name, branch, HTTPS URL, timeout, and output limits are loaded from an ignored local Git-policy overlay.

Before an operation, the bridge validates the repository layout, exact working-tree root, configured remote URL, and repository-local Git configuration. High-risk Git configuration surfaces such as aliases, credential helpers, hooks, filters, URL rewrites, includes, submodules, merge drivers, protocol overrides, SSH commands, external diff/filter commands, and unsafe remote overrides are rejected.

Git runs shell-free with system/global Git config disabled, interactive credential prompting disabled, HTTPS-only transport, bounded output/runtime, and one fail-fast Git operation per project. The bridge uses deterministic line-ending policy instead of inheriting system/global Git settings.

`git_sync_fast_forward` refuses dirty, detached, wrong-branch, divergent, or local-ahead states. It performs a fixed fetch, revalidates repository state, proves ancestry, uses `merge --ff-only --no-overwrite-ignore`, and verifies that final HEAD equals the previously verified fetched object ID.

A successful fast-forward intentionally mutates files inside the authorized project root. It does not authorize a remote write. Newly fetched repository content is still untrusted data; enabling both Git synchronization and executable project code means a later execution request can run code that arrived from the trusted configured remote.

## Persistence and recovery limits

Security-sensitive ceilings currently include:

- 32 active managed jobs globally;
- 512 retained terminal jobs maximum, 128 by default;
- 100 jobs per list response;
- 131072 characters per output page;
- 8 MiB per state file;
- 1024 candidate state files examined at startup;
- 64 MiB candidate bytes attempted during startup recovery;
- 4 MiB combined recovered stdout/stderr characters per admitted record;
- 120 seconds maximum per Phase 7 Git operation;
- 1 MiB maximum combined Git subprocess output.

Increasing/removing these limits requires review because persisted state and subprocess output are attacker-influenced and may also be modified by local actors.

## Public repository hygiene

Before every commit, verify that the diff contains no:

- API keys or bearer tokens;
- `.env` contents;
- Cloudflare/ngrok/tunnel credentials;
- private keys or certificates;
- real machine-specific configuration;
- real Git-policy overlay files;
- sensitive absolute paths;
- benchmark or job output containing secrets or proprietary/private material;
- runtime logs or job artifacts.

If a secret is ever committed, deleting the file in a later commit is **not sufficient**. Revoke/rotate the secret immediately and purge it from Git history where appropriate.

## High-risk changes

The following changes require dedicated negative tests and threat-model review:

- adding filesystem write/delete capabilities;
- broadening allowed roots;
- adding any shell, PowerShell, batch-script, or arbitrary command-string execution;
- adding arbitrary executable-path support to MCP calls;
- allowing caller-controlled environment inheritance or arbitrary environment injection;
- adding unrestricted network capabilities or elevated OS privileges to jobs;
- disabling path confinement, executable-resolution, persisted-state, or Git-repository validation checks;
- allowing commands/jobs to choose unrestricted working directories;
- persisting raw argv, credentials, parent environment, or reusable process identifiers;
- automatically reattaching to recovered PIDs/processes;
- increasing or removing execution/output/job/history/recovery/Git ceilings;
- weakening child-process termination or cancellation behavior;
- treating recovered state/output as trusted because it was previously persisted;
- adding Git push, arbitrary refspec/URL/argv, reset, clean, rebase, force, or history-rewrite capabilities;
- allowing Phase 7 to overwrite ignored local files or synchronize a dirty/divergent worktree;
- weakening the exact trusted-remote match or HTTPS-only Git transport policy;
- exposing the MCP endpoint publicly without authentication and encryption.
