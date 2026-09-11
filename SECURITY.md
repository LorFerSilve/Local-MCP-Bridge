# Security Policy

Local-MCP-Bridge is security-sensitive software because it exposes controlled local filesystem and process capabilities to AI clients.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest `main` branch unless a release policy is introduced later.

## Reporting a vulnerability

Do **not** publish exploit details, credentials, local paths, tokens, or reproducible attacks in a public issue.

When GitHub private vulnerability reporting is enabled for this repository, use a private security advisory/report. If that channel is unavailable, contact the repository owner privately before disclosing technical exploit details.

For non-sensitive hardening suggestions, a normal GitHub issue is appropriate.

## Security invariants

Changes must preserve these invariants unless an explicit security review documents why a broader capability is acceptable:

1. Filesystem access is denied outside configured project roots.
2. Paths are canonicalized and confined before authorization-sensitive use.
3. Symlinks, Windows junctions/redirecting reparse points, hard-linked read targets, and traversal components cannot be used to escape filesystem-read policy.
4. Process working directories are project-relative and must pass the Phase 4 confinement boundary.
5. A generic shell-command interface is unavailable; known shell executables and Windows shell-script targets are rejected by Phase 5.
6. MCP callers select explicitly configured executable aliases and cannot supply arbitrary host executable paths.
7. Processes are launched with direct argument vectors, disabled stdin, and a minimal environment rather than unrestricted inherited credentials.
8. Read sizes, process arguments, process output, execution time, and per-project concurrency are bounded.
9. Authentication secrets, tunnel credentials, local configuration, logs, and runtime job state are never required to be committed.
10. Process output is treated as untrusted data and bounded/sanitized before being returned.
11. Remote exposure must use authenticated encrypted transport while retaining local authorization checks.
12. `execute: true` is a high-trust permission and must never be represented as an operating-system sandbox.

## Phase 5 execution boundary

Phase 5 intentionally reduces command-injection and accidental privilege exposure without claiming hostile-code containment. The bridge exposes `run_process(project_id, executable_alias, args, cwd, timeout_seconds)` rather than `shell(command)`.

Allowlisted programs run under the operating-system account that started Local-MCP-Bridge. A programmable executable such as Python, pytest, a compiler, build system, or package manager may therefore execute project-controlled code that reads or writes host resources, accesses the network, or spawns descendants. Only grant `execute: true` to projects whose code you are willing to execute locally.

For exact executable selection, local-only configuration may pin an alias to an absolute executable path. MCP-visible metadata exposes the alias, not the pinned host path.

## Public repository hygiene

Before every commit, verify that the diff contains no:

- API keys or bearer tokens;
- `.env` contents;
- Cloudflare/ngrok/tunnel credentials;
- private keys or certificates;
- real machine-specific configuration;
- sensitive absolute paths;
- benchmark data containing secrets or proprietary/private material;
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
- disabling path confinement or executable-resolution checks;
- allowing commands to choose unrestricted working directories;
- increasing or removing execution/output/resource ceilings;
- weakening child-process termination or cancellation behavior;
- exposing the MCP endpoint publicly without authentication.
