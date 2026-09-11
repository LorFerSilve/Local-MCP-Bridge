# Security Model

## Trust model

The bridge assumes model-generated calls, repository content, MCP input, filenames, paths, future command arguments, and future child-process output may be malicious or incorrect. The deterministic local policy layer is the authorization boundary; security never depends on the model behaving correctly.

## Deny by default

Projects are registered explicitly. Current capability flags are `read`, `search`, `execute`, and `git`; missing permissions are denied. `search: true` requires `read: true`. Process execution and Git MCP tools remain unavailable in Phase 4 even if those reserved flags appear in local configuration.

## Project registry boundary

The registry maps AI-visible project IDs to canonical local roots. Absolute roots remain server-side. Roots must exist, be absolute directories, and may not overlap. Duplicate YAML keys, unknown project/permission keys, invalid IDs, and invalid permission combinations fail closed.

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

## Link and reparse-point policy

Phase 4 intentionally does not support symlink traversal, even when the link target remains inside the authorized root. On Windows, name-surrogate reparse points are treated as path redirections and denied; this includes junction/mount-point style redirection. Reparse metadata that is not a name surrogate is not automatically classified as a path redirect, avoiding a blanket policy that would unnecessarily reject unrelated metadata such as some cloud-file attributes.

Nested filesystem mount points are also denied. This keeps one registered project root from silently crossing into a separately mounted namespace.

## Hard-link policy

Regular files with more than one hard link are denied. Canonical pathname containment alone cannot distinguish a hard link created inside an authorized root from another name for the same file object outside that root on the same volume. Denying hard-linked regular files removes that cross-root read channel before process execution is introduced.

## Race-resistant file reads

`read_file` and search reads use the `PathGuard` confinement layer. Before content is read it:

- resolves and validates the project-relative path;
- captures file identity from non-following metadata;
- opens the file read-only, using `O_NOFOLLOW` where the platform provides it;
- compares the opened descriptor identity with the authorized pre-open identity;
- re-resolves/revalidates the pathname and compares identity again;
- only then reads a bounded number of bytes from the already-open descriptor.

If the path or file object changes during authorization, the operation fails before returning content. Once a validated descriptor is open, later pathname replacement does not redirect that descriptor to another file.

## Directory enumeration and recursive search

Directory listing captures bounded non-following entry metadata and verifies the directory identity before and after enumeration. If the directory is replaced during enumeration, the snapshot is discarded.

Recursive search stores only project-relative paths. It does not retain a previously resolved absolute pathname as ongoing authorization. Each queued directory is revalidated when enumerated and every queued file is reauthorized immediately before reading. Redirecting/restricted children are omitted.

## Sensitive-path defense in depth

Read permission does not expose every file in a root. Common secret-bearing locations/formats remain denied, including `.env`, `.git`, SSH/cloud credential directories, credential/token directories, private-key formats, Terraform state, and common service-account files. Templates such as `.env.example` remain readable.

This is defense in depth, not secret discovery. Operators should keep real secrets outside authorized roots whenever practical.

## Bounded I/O

`read_file` is UTF-8 text-only, rejects NUL/binary content, caps bytes and lines, and exposes line continuation. Listings and recursive search cap directory entries, files, per-file bytes, total bytes, query length, and result count. Search is literal substring matching rather than caller-controlled regex.

## Security boundary and residual risk

Phase 4 is **race-resistant application-level confinement**, not an operating-system sandbox. It substantially reduces path traversal, link/junction redirection, hard-link escape, and common check-then-use races, but it does not claim formal race-proof isolation against a concurrently malicious local process with arbitrary filesystem privileges.

That residual boundary matters for Phase 5: controlled child processes must still be narrowly allowlisted, run with project-scoped working directories, receive a filtered environment, and must not be treated as hostile native code safely contained by this library alone.

## Process execution

Phase 5 must use an executable plus argument vector rather than a shell command string. Required controls include executable allowlists, deterministic executable resolution, project-scoped working directories, filtered environment inheritance, timeouts, output caps, concurrency limits, and explicit cancellation/job state. A generic `shell(command)` interface remains out of scope.

## Git and network exposure

Future Git operations should be narrow and non-destructive by default. Development currently uses stdio; remote access must later use authenticated encrypted transport while retaining every local authorization check.

## Auditability and future writes

A later phase will add local audit records. Filesystem writes/deletes/renames are intentionally separate from read access and require their own reviewed policy and recovery semantics before introduction.
