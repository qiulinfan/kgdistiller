# Local-first deployment and recovery

kgdistiller is designed to run inside the repository that owns the authority
documents. The authority repository, not SQLite and not an Agent conversation,
is the unit to back up and synchronize.

## Recommended personal layout

```text
notes-repository/
├── notes/                         # Markdown, Typst, and LaTeX authorities
├── knowledge/
│   ├── sources.json               # committed source and taxonomy policy
│   ├── identities.json            # committed reviewed renames, when present
│   ├── alignments.json            # committed reviewed mappings
│   ├── graph/                     # committed deterministic qlkg-v2 snapshot
│   └── build/                     # ignored SQLite, plans, receipts, previews
└── vendor/kgdistiller/            # optional pinned Git submodule
```

Keep domain extraction Skills in the host repository. Use the canonical
`query-kgdistiller` and `ingest-kgdistiller` Skills from the installed package
or pinned submodule. Model providers, API keys, and personal source content do
not belong in the engine repository.

## Installation choices

For a reproducible host repository, pin kgdistiller as a submodule and invoke
the vendored source:

```sh
git submodule add -b main https://github.com/qiulinfan/kgdistiller.git vendor/kgdistiller
git submodule update --init --recursive
PYTHONPATH=vendor/kgdistiller/src python3 -m kgdistiller --repo-root . check
```

For one machine, install the command with `uv tool`:

```sh
uv tool install kgdistiller==0.3.0
kgdistiller --repo-root /absolute/path/to/notes check
```

Do not mix an unpinned global executable with a host that assumes a newer
schema or capability. `kgdistiller agent status` reports the active graph,
snapshot, alignment schemas, digests, and capabilities.

## Local Agent and browser configuration

Configure an Agent with an absolute repository path and stdio MCP:

```json
{
  "mcpServers": {
    "kgdistiller": {
      "command": "kgdistiller",
      "args": ["--repo-root", "/absolute/path/to/notes", "mcp"]
    }
  }
}
```

The MCP interface is read-only. Reviewed writes use a bounded ingest request
and the separate `ingest plan` / `ingest apply` commands. Never put an API key,
model token, or authority content in MCP settings.

The browser binds to `127.0.0.1` by default:

```sh
kgdistiller --repo-root /absolute/path/to/notes serve
```

Treat an explicit non-loopback bind as network publication. Put authentication,
TLS, request limits, and a reverse proxy in front of it; the built-in server is
not a multi-user authorization service.

## Git and cloud synchronization

Commit these files together:

- authority documents and referenced authored assets;
- `knowledge/sources.json`, `identities.json`, and `alignments.json`;
- every deterministic file under `knowledge/graph/`;
- a pinned submodule pointer or an explicit package version.

Ignore `knowledge/build/`, SQLite files, transaction staging directories,
plans, receipts, local snapshots, rendered pages, and provider caches. A Git
remote or ordinary encrypted backup service can synchronize the committed
authority repository. Avoid file-sync tools concurrently rewriting the same
working tree while `ingest apply` holds the local writer lock.

Before changing machines:

```sh
kgdistiller --repo-root . check
git status --short
git bundle create ../notes-backup.bundle --all
```

The bundle is optional but provides one portable, verifiable Git backup. Keep
private authority data only in storage appropriate for that data.

## Restore drill

1. Clone the authority repository or restore its Git bundle.
2. Initialize the pinned submodule or install the recorded kgdistiller version.
3. Delete any restored `knowledge/build/` directory; it is disposable.
4. Run `kgdistiller check` against the committed graph.
5. Run `kgdistiller agent status`; it rebuilds SQLite when absent.
6. Resolve several known IDs and build one bounded context bundle.
7. Run `ingest plan` for a disposable reviewed request before allowing writes.

Do not run `sync` first merely to make a failed `check` disappear. A mismatch
between committed authority and graph is evidence to review.

## Transaction recovery

The writer journal lives under `knowledge/build/kgdistiller-ingest/`. On the
next write, kgdistiller detects an interrupted installation and restores the
backed-up authority, alignment, graph, registry, and disposable index before
validating another request. Preserve the journal and its backup directory when
diagnosing a crash.

If recovery returns `rollback-failed`, stop all writers, copy the complete
repository, and inspect the journal targets. Restore the recorded backups or a
known-good Git revision before retrying. Never delete the journal to force a
mixed generation to look committed.

## Upgrade procedure

1. Require a clean authority worktree and a passing `check`.
2. Read the release compatibility and migration notes.
3. Update the package pin or submodule on a branch.
4. Run `agent status`, the full host workflow, and `check` before any sync.
5. Run a transaction `plan` with current candidate/query digests.
6. Apply only after reviewing the plan and backing up the authority repository.
7. Commit the engine pin and any deterministic graph migration atomically.

Downgrades are safe only when the older release declares every committed schema
readable. Keep the pre-upgrade Git revision until a restore drill succeeds.

