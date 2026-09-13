# Phase 11.1 — Controlled Real-Project Onboarding

## Goal

Phase 11.1 introduces one real development project to the bridge without enabling any mutation capability. The target project must be readable and searchable while process execution and Git synchronization remain disabled.

This phase does not add a new MCP capability. It validates that the existing project registry, path boundary, remote OAuth transport, and read/search tools can be applied to a real development repository before later Phase 11 steps progressively enable higher-trust capabilities.

## Required effective policy

The selected target must resolve to this effective permission state:

```yaml
permissions:
  read: true
  search: true
  execute: false
  git: false
```

`git: false` refers to the effective registry after the optional local Git overlay is applied. A Git overlay for the selected target therefore makes the project fail the Phase 11.1 readiness check even if the base YAML says `git: false`.

Allowed executable aliases may already be present in local configuration for later phases, but they provide no execution authority while `execute` remains false.

## Local configuration

Real project roots remain machine-local and must stay in the ignored file:

```text
config/config.yaml
```

Example:

```yaml
security:
  deny_by_default: true
  allow_arbitrary_shell: false

projects:
  selected-project:
    root: "C:/absolute/path/to/selected/project"
    permissions:
      read: true
      search: true
      execute: false
      git: false

    allowed_executables:
      - python
      - pytest
      - ruff

    execution:
      default_timeout_seconds: 60
      max_timeout_seconds: 300
      max_output_bytes: 262144
      max_concurrent_jobs: 1
```

The absolute root must never be copied into tracked configuration, issues, public logs, or MCP-facing metadata.

For Phase 11.1, do not add a `config/git.local.yaml` entry for the selected project. Git onboarding is deferred to Phase 11.4.

## Operator-side readiness check

Phase 11.1 adds:

```text
local-mcp-bridge-onboarding-check <project-id>
```

The command:

- loads the ordinary ignored project configuration;
- applies the optional local Git overlay so it checks effective permissions rather than only the base YAML;
- verifies that the selected logical project ID exists;
- requires read/search enabled and execute/Git disabled;
- reports only fixed-schema metadata;
- never prints the canonical project root, executable targets, Git URLs, or file content;
- performs no network request and mutates no configuration.

Successful output has this shape:

```json
{"allowed_executable_count":3,"configured_project_count":2,"ok":true,"permissions":{"execute":false,"git":false,"read":true,"search":true},"project_id":"selected-project","reason":"ready"}
```

Exit codes:

- `0` — target is ready for the Phase 11.1 live read/search smoke test;
- `1` — target exists but is not read/search-only, or is not configured;
- `2` — invalid project ID or invalid local configuration.

## Live Claude smoke sequence

After the readiness check passes, restart the remote runtime so it loads the current local registry. Keep the existing OAuth and HTTPS tunnel boundary unchanged.

In Claude, use only read-only operations against the selected target:

1. `list_projects` — confirm the project appears by logical ID.
2. `get_project` — confirm read/search are enabled and execute/Git are disabled.
3. `list_directory` — inspect one harmless directory.
4. `read_file` — read a known non-sensitive text file.
5. `search_text` — search for a harmless literal expected to exist in the project.

The smoke test fails if the model receives an absolute local root, if a restricted/sensitive entry leaks, if execution or Git is enabled, or if a read/search call escapes the configured project boundary.

## Completion gate

Phase 11.1 is complete only after all of the following are true:

- repository implementation and tests for the onboarding check are merged and green;
- one real local project is present in ignored `config/config.yaml`;
- `local-mcp-bridge-onboarding-check <project-id>` exits `0`;
- the real Claude connector sees the selected project with the exact read/search-only permission state;
- Claude successfully completes directory listing, one safe file read, and one safe text search;
- no execution or Git action is enabled or required.

Until the live target-specific checks are recorded, Phase 11.1 remains **in progress** rather than complete.

## Security invariants

Phase 11.1 preserves all earlier boundaries:

- project roots remain server-side;
- project IDs are the only MCP-facing identities;
- no filesystem-write tool exists;
- no generic shell exists;
- executable aliases do not grant authority when execution is disabled;
- Git remains a dedicated capability and stays disabled for the target;
- the listener remains loopback-only;
- public reachability remains external through HTTPS ingress;
- OAuth authentication never overrides project-level permissions;
- audit/runtime state remains local and is not exposed as model context.

## Next dependency

After the live Phase 11.1 proof succeeds, Phase 11.2 may enable controlled process execution for the same selected project. That change must be explicit, narrowly allowlisted, bounded, and validated separately.
