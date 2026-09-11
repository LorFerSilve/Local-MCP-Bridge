# Architecture

## Goal

Local-MCP-Bridge is a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources. The model is never a trusted security principal.

## High-level architecture

```text
MCP client / AI agent
        |
        v
+---------------------------+
| Runtime composition root  |
| - loads ignored config    |
| - builds registry         |
+-------------+-------------+
              v
+---------------------------+
| MCP tool layer            |
| - project metadata        |
| - read-only filesystem    |
+-------------+-------------+
              v
+---------------------------+
| Filesystem policy layer   |
| - permissions             |
| - sensitive-path filter   |
| - bounded I/O/search      |
+-------------+-------------+
              v
+---------------------------+
| PathGuard confinement     |
| - lexical validation      |
| - lstat component checks  |
| - reparse/link rejection  |
| - canonical containment   |
| - identity verification   |
+-------------+-------------+
              v
+---------------------------+
| Local host filesystem     |
+-------------+-------------+
              v
       Authorized projects
```

## Active Phase 4 modules

```text
src/local_mcp_bridge/
├── config.py
├── registry.py
├── runtime.py
├── server.py
├── security/
│   ├── __init__.py
│   └── paths.py
└── tools/
    └── filesystem.py
```

`config.py` parses local YAML and constructs the immutable project registry. `registry.py` maps logical IDs to canonical roots while keeping absolute host paths private. `server.py` is a pure MCP factory and never reads machine-local configuration; `runtime.py` is the explicit composition root that does.

`tools/filesystem.py` owns project permissions, sensitive-path filtering, text/binary policy, output shaping, and resource ceilings. `security/paths.py` owns the lower-level path trust boundary and is reused by all filesystem operations.

## Project identity boundary

MCP clients address logical IDs plus relative paths:

```text
read_file(project_id="example-project", path="src/main.py")
```

They never choose an absolute host root per call. The ignored local configuration maintains the private mapping from project ID to root.

## Phase 4 path flow

```text
relative caller path
      |
      v
lexical validation
      |
      v
sensitive-path policy
      |
      v
lstat each component
      |
      +-- deny symlink / redirecting reparse point / nested mount
      |
      v
strict canonical resolution
      |
      +-- prove inside configured root
      |
      v
validate regular file/directory
      |
      +-- deny hard-linked regular files for reads
      |
      v
operation-specific identity checks
      |
      v
bounded I/O
```

## File-read identity protocol

A read does not treat a successful `Path.resolve()` as permanent authorization. `PathGuard.read_bounded` performs a check-open-check sequence:

```text
lstat/resolve path
      |
      v
capture FileIdentity(dev, inode, type)
      |
      v
os.open(read-only, O_NOFOLLOW where available)
      |
      v
fstat(open descriptor)
      |
      +-- identity must match
      |
      v
re-resolve/revalidate pathname
      |
      +-- identity/path must still match
      |
      v
read bounded bytes from verified descriptor
```

If a file is replaced between authorization and open, content is not read. After the descriptor is validated, later renaming of the pathname does not redirect the already-open descriptor.

## Directory and recursive-search protocol

Directory snapshots use non-following entry metadata and are bounded during enumeration. The directory identity is checked before and after enumeration; a replacement invalidates the entire snapshot.

Recursive search deliberately stores project-relative paths rather than previously authorized absolute paths. Every directory is revalidated when enumerated and every file is reauthorized immediately before reading. This prevents an earlier safe lookup from becoming a stale authorization after a directory changes into a symlink/junction.

## Windows reparse behavior

Phase 4 denies Windows reparse points that act as **name surrogates**, which are path-redirection objects such as junction/mount-point style entries. It does not blanket-deny every reparse attribute because unrelated filesystem metadata can also use reparse points without redirecting pathname resolution.

## Hard links

Hard links do not redirect pathname resolution, so canonical containment alone cannot prove that a file object has no alias outside the authorized root. Phase 4 therefore denies regular files with link count greater than one. This is intentionally conservative.

## Test/runtime isolation

```text
pytest -> pure server factory + tmp_path registries
runtime -> load_runtime_registry() -> local config
```

Machine-local `config/config.yaml` cannot break pytest collection. The actual runtime still validates local config and fails closed.

## Security boundary

This design is application-level race-resistant confinement. It is not a kernel sandbox and does not promise containment of hostile native code or a concurrently malicious local administrator/process. Phase 5 controlled execution must therefore remain narrow and policy-driven rather than assuming `PathGuard` makes arbitrary commands safe.

## Future execution and jobs

The intended interfaces remain capability-oriented:

```text
start_job(project_id, executable, args)
get_job_status(job_id)
read_job_output(job_id)
cancel_job(job_id)
git_status(project_id)
git_pull(project_id)
```

A generic `shell(command)` interface is intentionally excluded.

## Transport boundary

Development uses local MCP stdio. Browser/cloud MCP clients will later connect only through authenticated encrypted transport/tunneling while all project/path permissions remain enforced locally.
