# Security Policy

Local-MCP-Bridge is security-sensitive software because it is intended to expose controlled local filesystem and process capabilities to remote or local AI clients.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest `main` branch unless a release policy is introduced later.

## Reporting a vulnerability

Do **not** publish exploit details, credentials, local paths, tokens, or reproducible attacks in a public issue.

When GitHub private vulnerability reporting is enabled for this repository, use a private security advisory/report. If that channel is not available, contact the repository owner privately before disclosing technical exploit details.

For non-sensitive hardening suggestions, a normal GitHub issue is appropriate.

## Security invariants

Changes must preserve these invariants unless an explicit security review documents why a broader capability is acceptable:

1. Filesystem access is denied outside configured project roots.
2. Paths are canonicalized before authorization decisions.
3. Symlinks, Windows junctions/reparse points, and traversal components cannot be used to escape an allowed root.
4. Process working directories remain inside an allowed project root.
5. Arbitrary shell execution is disabled by default.
6. Executables and high-risk operations are explicitly allowlisted.
7. Read sizes, process output, execution time, and concurrency are bounded.
8. Authentication secrets, tunnel credentials, local configuration, logs, and runtime job state are never required to be committed.
9. Logs must avoid recording secrets or sensitive file contents by default.
10. Remote exposure must use authenticated encrypted transport.

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
- adding shell or PowerShell execution;
- adding arbitrary executable support;
- adding network access from executed jobs;
- exposing environment variables;
- disabling path canonicalization or reparse-point checks;
- allowing commands to choose unrestricted working directories;
- exposing the MCP endpoint publicly without authentication.
