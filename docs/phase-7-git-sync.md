# Phase 7 — Git synchronization boundary

## Goal

Phase 7 gives an MCP client a narrow way to inspect and synchronize an explicitly authorized local Git working tree without exposing a generic `git` command surface. The caller chooses only a logical `project_id`; trusted remote, branch, and transport policy remain machine-local.

## Public tools

Phase 7 adds exactly three Git operations:

- `git_status(project_id)` — path-free branch/HEAD/cleanliness and optional ahead/behind counts;
- `git_fetch(project_id)` — fetch the configured branch from the trusted HTTPS URL into its configured remote-tracking ref;
- `git_sync_fast_forward(project_id)` — fetch and advance a clean checked-out configured branch only when the fetched head is a strict fast-forward.

There is intentionally no MCP tool for push, commit, checkout/switch, reset, clean, rebase, cherry-pick, branch/tag mutation, force operations, arbitrary refspecs, arbitrary URLs, or caller-supplied Git argv.

## Local policy overlay

Git is enabled through a separate ignored configuration file rather than request parameters:

```powershell
Copy-Item config/git.example.yaml config/git.local.yaml
```

Example:

```yaml
projects:
  example-project:
    remote: origin
    branch: main
    remote_url: "https://github.com/example/example-project.git"
    timeout_seconds: 60
    max_output_bytes: 262144
```

`LOCAL_MCP_BRIDGE_GIT_CONFIG` may select another local policy file. The overlay rejects duplicate or unknown keys, unknown projects, non-HTTPS URLs, embedded credentials/query/fragment, unsafe branch/remote names, and limits outside hard ceilings.

The trusted URL must match the repository's existing `remote.<name>.url` exactly. The MCP caller cannot replace the URL, remote, branch, protocol, or refspec.

## Transport and Git process policy

Git receives a minimal environment and a fixed command-line policy:

- system and global Git config are disabled;
- terminal prompting, inherited credential helpers, and AskPass are disabled;
- allowed transport protocols are restricted to HTTPS;
- pagers are neutralized;
- hooks, fsmonitor, reflog updates for Phase 7 invocations, submodule recursion, automatic maintenance, and automatic GC are disabled;
- Git is resolved only from validated absolute `PATH` directories outside the authorized project root;
- the Git executable must be a non-redirecting regular executable and its identity is rechecked immediately before launch;
- runtime and combined stdout/stderr are bounded;
- operations use direct argv execution, never a shell.

Because system/global Git configuration is deliberately unavailable, line-ending behavior is made deterministic by the runner: `core.autocrlf=true` on Windows and `core.autocrlf=false` on POSIX.

Private repositories that require interactive or inherited credentials are outside the Phase 7 contract; the security defaults should not be weakened to accommodate them.

## Repository trust validation

Before every exposed Git operation, the bridge verifies the repository again. Phase 7 supports a normal working tree whose `.git` entry is a real directory; Gitfile/worktree indirection is rejected.

The bridge checks that:

1. `.git`, `.git/objects`, and `.git/refs` are non-redirecting directories;
2. critical metadata such as `HEAD`, `config`, and an existing `index` are regular, non-redirecting, non-hard-linked files;
3. external object alternates and common-directory indirection are absent;
4. existing configured remote-ref directory components are non-redirecting directories;
5. `git rev-parse --show-toplevel` resolves exactly to the configured authorized root;
6. repository-local config contains no disallowed command-execution or transport-redirection capability;
7. the configured local remote URL exactly matches the trusted local overlay.

## Repository-local config denial policy

Repository-local Git config is untrusted. Phase 7 rejects high-risk namespaces/settings including aliases, credentials, filters, hooks, include/includeIf, submodules, URL rewrites, merge drivers, HTTP/protocol overrides, SSH commands, external diff/filter commands, alternate-ref commands, worktree/partial-clone redirection, branch merge options, and unsafe remote overrides.

This is defense in depth in addition to fixed command-line config and a minimal child environment.

## Status privacy

`git_status` does not return changed filenames. It returns only current/configured branch, HEAD object ID, clean/dirty state, staged/unstaged/untracked counts, remote-tracking presence, and ahead/behind counts when available.

## Fetch semantics

`git_fetch` fetches only:

```text
refs/heads/<configured-branch>
    -> refs/remotes/<configured-remote>/<configured-branch>
```

The trusted HTTPS URL is supplied by local policy. Tags are not fetched, submodules are not recursively fetched, and `FETCH_HEAD` is not written.

## Fast-forward synchronization

`git_sync_fast_forward` follows this conservative flow:

```text
validate repository + local config
        |
        v
require configured branch checked out
        |
        v
require staged=0, unstaged=0, untracked=0
        |
        v
fetch configured branch into configured remote-tracking ref
        |
        v
revalidate repository + branch + clean worktree
        |
        v
capture local HEAD and verified fetched object ID
        |
        v
require HEAD is ancestor of fetched remote head
        |
        v
merge --ff-only --no-edit --no-stat --no-overwrite-ignore
        |
        v
verify new HEAD == previously verified fetched object ID
```

`--no-overwrite-ignore` is important: if the fetched tree starts tracking a path currently occupied by a local ignored file, Git must abort instead of silently replacing that ignored local data.

If local history diverges, local history is ahead, the worktree is dirty, the branch changes, an ignored local file obstructs the target, or any verification fails, synchronization stops. Phase 7 never repairs these states with reset, merge commits, rebase, clean, stash, or history rewriting.

## Concurrency and resource boundary

Only one Phase 7 Git operation may execute per project at a time; concurrent requests fail rather than build an unbounded queue. The hard Git timeout ceiling is 120 seconds and the combined stdout/stderr ceiling is 1 MiB. These are bridge-level resource controls, not CPU/RAM/network/disk quotas for Git.

## Relationship to generic execution

If generic `execute` permission is enabled, an allowlist containing `git`/`git.exe` is rejected by the Phase 7 runtime policy so an MCP caller cannot bypass the narrow Git API through `run_process` or `start_job`.

An allowlisted interpreter or other programmable tool can still invoke Git itself. Generic execution is a high-trust capability and is not an OS sandbox.

## Residual risks

Phase 7 is application-level policy, not repository or host isolation. Another process with equivalent OS privileges can race metadata between checks, and another bridge instance is not covered by the in-process per-project lock. Git itself is complex native software processing repository and network data.

A successful fast-forward intentionally modifies files inside the authorized working tree. Newly fetched code remains untrusted content. If the operator also enables execution for the project, a later execution request can run code that arrived from the trusted configured remote.

Phase 7 does not authorize remote mutation or writes outside the authorized working tree through its dedicated Git surface.
