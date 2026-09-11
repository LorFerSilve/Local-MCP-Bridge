# Security Model

## Trust model

The bridge assumes model-generated calls, repository content, MCP input, filenames, paths, command arguments, and child-process output may be malicious or incorrect. Deterministic local policy is the authorization boundary; security never depends on the model behaving correctly.

## Deny by default

Projects are registered explicitly. Capability flags are `read`, `search`, `execute`, and `git`; missing permissions are denied. `search: true` requires `read: true`. Phase 5 activates `execute`, but only when the project also has a non-empty executable allowlist.

`security.deny_by_default` cannot be disabled and `security.allow_arbitrary_shell` cannot be enabled. Configurations attempting either change fail closed.

## Project registry boundary

The registry maps AI-visible project IDs to canonical local roots. Absolute roots remain server-side. Roots must exist, be absolute directories, and may not overlap.

Phase 5 also stores executable rules and execution settings in the internal project record. Public metadata exposes executable aliases only; pinned executable paths remain local.

Duplicate YAML keys, unknown project/permission/execution keys, invalid IDs, invalid permission combinations, invalid executable rules, and out-of-range execution limits fail closed.

No local config means an empty runtime registry. An explicitly selected invalid config fails closed. The pure MCP server factory does not read machine-local config, keeping pytest hermetic.

## Phase 4 filesystem authorization

Every read-only filesystem request follows this sequence:

1. resolve the project ID and required permission;
2. parse a project-relative path and reject unsafe lexical forms;
3. reject common sensitive/credential paths;
4. inspect each existing path component without following redirections;
5. reject symbolic links, redirecting Windows name-surrogate reparse points/junctions, and nested filesystem mount points;
6. strictly resolve the effective path and prove canonical containment in the configured root;
7. reject unsupported file types and hard-linked regular files;
8. perform operation-specific identity checks and bounded I/O;
9. return project-relative metadata only.

Rejected lexical forms include POSIX absolute paths, Windows drive/UNC paths, `..`, control/NUL characters, NTFS ADS/colon syntax, reserved DOS device names, and components ending in a Windows-ambiguous space or period.

## Link, reparse, hard-link, and race policy

Phase 4 does not support symlink traversal, even when the link target remains inside the authorized root. Windows name-surrogate reparse points and nested filesystem mount points are denied. Regular files with more than one hard link are denied for filesystem reads.

`read_file` and search reads capture non-following file identity, open read-only with `O_NOFOLLOW` where available, compare descriptor identity, revalidate the pathname, and only then read bounded content. Directory enumeration verifies directory identity before and after enumeration. Recursive search stores project-relative paths and reauthorizes each entry at point of use.

## Sensitive-path defense in depth

Read permission does not expose every file in a root. Common secret-bearing locations/formats remain denied, including `.env`, `.git`, SSH/cloud credential directories, credential/token directories, private-key formats, Terraform state, and common service-account files. Templates such as `.env.example` remain readable.

This is defense in depth, not secret discovery. Operators should keep real secrets outside authorized roots whenever practical.

## Phase 5 execution authorization

The only execution primitive is:

```text
run_process(project_id, executable_alias, args, cwd, timeout_seconds)
```

There is intentionally no `shell(command)` tool and the caller never supplies a host executable path.

An execution request must pass all of the following gates:

1. the project ID exists;
2. `permissions.execute` is true;
3. the requested executable alias is configured for that project;
4. the alias/target is not a known shell executable;
5. argv satisfies hard count/size/control-character limits;
6. `timeout_seconds` is within the project's configured maximum;
7. `cwd` is a project-relative directory that passes Phase 4 `PathGuard` confinement;
8. the configured executable resolves to a safe regular executable file;
9. the executable identity is rechecked immediately before process creation;
10. the process is started directly with an argv vector and a minimal environment.

Policy failures are returned as MCP errors without exposing configured host paths.

## Executable allowlist policy

Phase 5 supports two local allowlist forms.

### Unpinned names

```yaml
allowed_executables:
  - python
  - pytest
```

Unpinned names are resolved through a constrained `PATH`. Empty/relative entries, redirecting PATH directories, unavailable entries, and PATH directories inside the authorized project root are removed. This prevents a project-local executable with a matching name from silently taking precedence through normal PATH lookup.

### Pinned aliases

```yaml
allowed_executables:
  python: "C:/absolute/path/to/python.exe"
```

Pinned targets are canonicalized and validated in ignored local configuration. Only the alias is visible to MCP callers. This is the preferred form when the intended executable lives inside a project-local virtual environment or when exact executable selection matters.

The executable file is revalidated immediately before launch. Cross-platform subprocess APIs do not provide the same portable descriptor-based execution semantics used by Phase 4 reads, so replacement by a concurrently privileged local actor remains a residual race rather than a formally eliminated one.

## No-shell guarantee

Processes are created with `asyncio.create_subprocess_exec`, which supplies the executable and argv directly to the operating system rather than asking a shell to parse a command string.

Known shell targets such as `cmd.exe`, PowerShell, `sh`, and `bash` are rejected even when configured behind an alias. Shell metacharacters in a normal argument therefore remain argument data rather than becoming extra commands.

This does **not** make interpreters equivalent to a sandbox. If the operator allowlists Python, Node, a compiler, pytest, a build system, or another programmable tool, that executable can run code with the privileges of the bridge account.

## Working-directory policy

The process `cwd` is always expressed as a project-relative path and is passed through `PathGuard`. Traversal, symlink, junction, reparse, nested-mount, and other unsafe directory paths are rejected before process creation.

Once the process starts, however, the child is not restricted by `PathGuard`. It can use ordinary operating-system APIs and can access anything available to the bridge account. `cwd` confinement prevents the bridge from *starting* the process outside the selected project; it is not a filesystem sandbox for the child.

## Child environment policy

Phase 5 does not inherit the full bridge environment. The child receives only a small set of platform/runtime variables plus a constrained PATH and Python hardening variables. There is no MCP parameter for arbitrary environment overrides.

This substantially reduces accidental leakage of tokens, API keys, cloud credentials, and bridge-specific secrets through ordinary environment inheritance. It is not a guarantee against a child explicitly reading credentials from the filesystem or another host service accessible to the bridge account.

## Execution resource limits

Phase 5 enforces both configurable bounds and hard application ceilings. Current hard ceilings include:

- timeout: 300 seconds;
- combined captured stdout/stderr: 1 MiB;
- per-project concurrent one-shot processes: 4;
- arguments: 64;
- one argument: 4096 characters;
- combined argument characters: 16384.

The example policy is stricter. `stdin` is `DEVNULL`, preventing interactive prompts from hanging while waiting for model-supplied input.

If the combined output budget is exhausted, the process is terminated and a structured `output_limit` result is returned. A timeout produces a structured `timeout` result. Non-zero normal exits remain ordinary process results rather than policy errors.

## Process termination boundary

On POSIX, child processes start in a new session and timeout/output-limit termination targets that process group. On Windows, Phase 5 starts a new process group but Python's portable kill primitive only guarantees termination of the direct child.

Accordingly, Phase 5 does not claim durable descendant-process containment. Stronger Windows process-tree/job-object supervision and durable cancellation belong to Phase 6/runtime hardening.

## Child-output policy

Process output is untrusted data. Capture happens under a byte budget before decoding. Unsupported control/terminal characters are escaped before MCP return. Direct appearances of the configured project-root path are redacted as `<project-root>`.

Redaction is defense in depth. A malicious child can encode or transform host data in arbitrary ways and therefore cannot be made non-exfiltrating through generic string replacement.

## Security boundary and residual risk

Phase 5 is **controlled application-level execution**, not an operating-system sandbox. Granting `execute: true` is materially different from granting read/search access. It authorizes execution of selected programs under the bridge account, and those programs may execute project-controlled code.

Therefore, only enable execution for projects you are willing to run locally. Phase 5 mitigates unapproved executable selection, shell injection, working-directory escape at launch, accidental environment-secret inheritance, excessive argv/output/runtime, and some executable-substitution risks. It does not contain intentionally hostile code, network access, filesystem writes performed by the child, or all descendant processes.

## Git and network exposure

Dedicated Git MCP operations remain deferred to Phase 7 so Git-specific destructive actions receive narrow policy. Development transport remains local stdio; future remote access must use authenticated encrypted transport while retaining all local authorization checks.

## Auditability, jobs, and future writes

Phase 6 adds persistent job identity/state, durable output retrieval, cancellation, cleanup, and stronger process supervision. A later hardening phase adds local audit records. Direct filesystem write/delete/rename MCP primitives remain separate and require their own reviewed policy/recovery semantics before introduction.
