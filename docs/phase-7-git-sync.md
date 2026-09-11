# Phase 7 — Git synchronization boundary

## Goal

Phase 7 gives an MCP client a narrow way to inspect and synchronize an explicitly
authorized local Git working tree without exposing a generic `git` command surface.
The caller chooses only a logical `project_id`; trusted remote, branch, and transport
policy remain machine-local.

## Public tools

Phase 7 adds exactly three Git operations:

- `git_status(project_id)` — returns path-free branch/HEAD/cleanliness and optional ahead/behind counts;
- `git_fetch(project_id)` — fetches the configured branch from the trusted HTTPS URL into its configured remote-tracking ref;
- `git_sync_fast_forward(project_id)` — fetches and advances a clean checked-out configured branch only when the remote head is a strict fast-forward.

There is intentionally no MCP tool for push, commit, checkout, switch, reset, clean,
rebase, cherry-pick, branch creation/deletion, tag mutation, force operations, arbitrary
refspecs, arbitrary URLs, or caller-supplied Git argv.

## Local policy overlay

Git is enabled through a second ignored configuration file rather than through request
parameters. Copy the tracked template:

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

`LOCAL_MCP_BRIDGE_GIT_CONFIG` may point to another local policy file. The explicit
overlay is fail-closed: unknown project IDs, unknown keys, non-HTTPS URLs, embedded
credentials, unsafe branch/remote names, and limits outside hard ceilings are rejected.

The trusted URL must match the repository's existing `remote.<name>.url` exactly. This
prevents repository-controlled local config from silently redirecting synchronization to
a different destination.

## Transport and credential policy

Phase 7 allows HTTPS only. Git receives a minimal child environment and:

- system and global Git config are disabled;
- terminal prompting is disabled;
- credential helpers are cleared;
- AskPass is disabled;
- allowed transport protocols are restricted to HTTPS;
- Git pagers are neutralized for non-interactive operation;
- submodule recursion, automatic maintenance, and automatic GC are disabled.

This deliberately favors a small auditable public-repository synchronization boundary.
Private repositories that require interactive or inherited credentials are outside the
Phase 7 contract and should not be enabled by weakening these defaults.

## Repository trust validation

Before every exposed Git operation, the bridge verifies the local repository again.
Phase 7 currently supports a normal working tree whose `.git` entry is a real directory.
Gitfile/worktree indirection is rejected rather than followed.

The bridge checks that:

1. `.git`, `.git/objects`, and `.git/refs` are non-redirecting directories;
2. critical metadata such as `HEAD`, `config`, and an existing `index` are regular,
   non-redirecting, non-hard-linked files;
3. external object alternates are absent;
4. `git rev-parse --show-toplevel` resolves exactly to the configured authorized root;
5. repository-local config contains no capability that can turn an apparently harmless
   Git operation into arbitrary command execution or transport redirection;
6. the configured local remote URL exactly matches the trusted local overlay.

## Repository-local config denial policy

Repository-local Git config is untrusted input. Phase 7 rejects high-risk namespaces or
settings including aliases, credential helpers/config, filters, hooks, include/includeIf,
submodules, URL rewrites, merge drivers, HTTP/protocol overrides, SSH commands, external
diff/filter commands, alternate-ref commands, and remote proxy/upload-pack/receive-pack
overrides.

This is defense in depth in addition to fixed command-line config and a minimal child
environment. A future phase may expand supported Git layouts only after a separate
security review.

## Status privacy

`git_status` does not return changed filenames. It returns only:

- current branch or detached state;
- configured branch;
- HEAD object ID;
- clean/dirty boolean;
- staged, unstaged, and untracked counts;
- whether the configured remote-tracking ref exists;
- ahead/behind counts when that ref exists.

Avoiding filenames keeps repository-controlled path strings out of the high-level Git
status surface and avoids leaking local paths through routine synchronization metadata.

## Fetch semantics

`git_fetch` uses a fixed command shape. It fetches only:

```text
refs/heads/<configured-branch>
    -> refs/remotes/<configured-remote>/<configured-branch>
```

The command uses the trusted HTTPS URL from local policy directly. Tags are not fetched,
submodules are not recursively fetched, and `FETCH_HEAD` is not written. The caller cannot
change the remote, URL, branch, protocol, refspec, flags, or destination ref.

## Fast-forward synchronization

`git_sync_fast_forward` is deliberately conservative:

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
recheck branch + clean worktree
        |
        v
require HEAD is ancestor of fetched remote head
        |
        v
merge --ff-only
        |
        v
verify new HEAD == previously verified fetched object ID
```

If local history has diverged, the local branch is ahead, the worktree is dirty, the
branch changes during the operation, or any verification fails, synchronization stops.
Phase 7 never resolves these states with reset, merge commits, rebase, clean, stash, or
history rewriting.

## Process and resource boundary

Git itself is resolved from a constrained absolute `PATH` outside the authorized project
root. The executable must be a non-redirecting regular executable and its identity is
rechecked before launch. Operations are shell-free and use a bounded timeout and combined
stdout/stderr budget. Only one Phase 7 Git operation may execute per project at a time;
concurrent requests fail rather than queue indefinitely.

## Relationship to process execution

Dedicated Git synchronization is independent of generic process execution. If generic
`execute` permission is enabled, an allowlist containing `git`/`git.exe` is rejected by
the Phase 7 runtime policy so an MCP caller cannot bypass the narrow Git API through
`run_process` or `start_job`.

An operator can still run arbitrary trusted programs that themselves invoke Git; Phase 7
cannot make programmable process execution into an OS sandbox. The guarantee is that the
bridge does not intentionally publish a second generic Git primitive.

## Residual risks

Phase 7 is application-level policy, not repository or host isolation. A local actor with
equivalent OS privileges can race metadata between checks. Git itself processes repository
objects and metadata. The bridge therefore rejects known command-execution/configuration
surfaces and keeps operations narrow, but it does not claim that intentionally hostile Git
implementations or an already-compromised host are contained.

A successful fast-forward modifies files inside the authorized working tree. That is the
intended Phase 7 mutation. It does not authorize writes outside that working tree or any
remote mutation.
