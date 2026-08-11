# Local-first deployment and recovery

kgdistiller is designed around a portable authority store. The store—not
SQLite and not an Agent conversation—is the unit to back up and synchronize.
It can be the notes repository itself or a dedicated private Git repository
created from an existing knowledge project.

Design rationale, rejected alternatives, versioned contracts, and the v1
implementation map are recorded in
[portable-store-development.md](portable-store-development.md).

## Recommended personal layout

```text
personal-knowledge-store/
├── notes/                         # Markdown, Typst, and LaTeX authorities
├── knowledge/
│   ├── sources.json               # committed source and taxonomy policy
│   ├── identities.json            # committed reviewed renames, when present
│   ├── alignments.json            # committed reviewed mappings
│   ├── graph/                     # committed deterministic qlkg-v2 snapshot
│   ├── documents.jsonl            # committed authority inventory
│   ├── embedding-policy.json      # committed credential-free vector policy
│   ├── embeddings/                # committed exact float32 vector objects
│   ├── store.json                 # committed qlkg-store-v1 generation
│   ├── .gitignore                 # keeps build/ local; existing rules preserved
│   └── build/                     # ignored SQLite, plans, receipts, previews
```

Create or refresh this layout in place with:

```sh
kgdistiller --repo-root . check
kgdistiller --repo-root . store snapshot
kgdistiller --repo-root . store verify
```

To migrate from a different notes project, write a self-contained copy to a
separate directory:

```sh
kgdistiller --repo-root /absolute/path/to/notes store snapshot \
  --output /absolute/path/to/personal-knowledge-store
```

The destination cannot be nested inside the source project. Only registered,
already-ingested `.md`, `.typ`, and `.tex` authorities are copied. Once the
dedicated store becomes primary, run future ingest operations against it so
source, graph, inventory, and embeddings advance as one generation.

Use the complete Skill suite and Codex presets shipped by the installed
kgdistiller product. Model providers, API keys, personal source content, and
generated project graphs do not belong in the product repository.

## Installation choices

For a reproducible knowledge project, record and install an exact kgdistiller
package version and release commit:

```sh
uv tool install kgdistiller==0.3.0
kgdistiller --repo-root . check
kgdistiller codex link
kgdistiller codex doctor
```

For one machine, install the command with `uv tool`:

```sh
uv tool install kgdistiller==0.3.0
kgdistiller --repo-root /absolute/path/to/notes check
```

Do not mix an unpinned global executable with a project that assumes a newer
schema or capability. `kgdistiller agent status` reports the active graph,
snapshot, alignment schemas, digests, and capabilities.

## Machine-local profile

The default machine-local profile is
`knowledge/build/local-profile.json`. `knowledge/.gitignore` already excludes
`build/`, so this file must not enter the portable store or a Git commit. A
minimal profile is:

```json
{
  "schema": "qlkg-local-profile-v1",
  "database": "knowledge.sqlite",
  "portable_store": "/absolute/path/to/private-store",
  "embedding_profile": "primary",
  "provider_profiles": {
    "primary": {
      "adapter": "openai-compatible",
      "model": "example-embedding-model",
      "dimensions": 1536,
      "base_url": "https://provider.example/v1",
      "credential_env": "EMBEDDING_API_KEY"
    }
  }
}
```

Relative `database` and `portable_store` paths are resolved from the profile
file's directory. Run `kgdistiller profile status` after editing the file and
again from a fresh process to confirm the effective paths and profile.
Command-line `--database`, `--store`, and `--embedding-profile` values take
precedence. Use `--local-profile` to select another machine-local file.

Only the credential environment-variable name belongs in the profile. The
credential value is read at request time and is excluded from status, errors,
digests, receipts, and portable artifacts. For network adapters, the
machine-local status `provider_config_sha256` binds the adapter, model,
dimensions, and normalized base URL—not the credential or its
environment-variable name.

Production provider URLs must use HTTPS. The only plaintext exception is an
explicit numeric loopback address (`127.0.0.0/8` or `::1`) for a machine-local
fixture; hostnames such as `localhost`, private-network addresses, and remote
hosts do not qualify. URL normalization lowercases the scheme and IDNA host,
removes the default port, and removes trailing path slashes before the
configuration digest is calculated.

The bearer credential is accepted only as a bounded ASCII token without
whitespace or control characters. Network operations have an inactivity
timeout. After operating-system name resolution and socket setup, HTTP status,
header, and body reads additionally share one monotonic wall-clock deadline, so
a peer cannot evade it by continuously sending tiny chunks. Resolver and
multi-address connection latency remain operating-system governed; if they
return after the deadline, the adapter reports a timeout. HTTP framing failures,
response-size and JSON-depth limits, and invalid numeric vectors are reported as
structured, secret-safe provider errors; raw response bodies and exception
chains are not kept in output or receipts.

The `deterministic-fixture` adapter is credential-free and exists only for
repeatable acceptance tests. Production profiles should select a reviewed real
adapter such as `openai-compatible`.

## Explicit embedding maintenance

The portable, credential-free policy defaults to
`knowledge/embedding-policy.json`. It declares each profile name, provider,
model, dimensions, required node types, minimum coverage, and whether the
profile is required. Those vector-space fields must agree with the
same-named machine-local provider profile. Use the global
`--embedding-policy path/to/policy.json` option when a repository keeps the
policy elsewhere; a relative path is resolved from `--repo-root`.

Run status before making a provider available:

```sh
kgdistiller --repo-root . embedding status
kgdistiller --repo-root . embedding status --namespace personal
```

Status does not create or refresh the local index, construct a provider, make a
network call, or mutate embeddings. It reports `ready`, `missing`, `stale`, and
`incompatible` eligible slots plus `unmanaged` rows, grouped by policy profile
and node type. Coverage is compared with the policy threshold; zero eligible
nodes produce `not-applicable`, not 100%.

Only the explicit sync command creates or replaces document vectors:

```sh
kgdistiller --repo-root . embedding sync
kgdistiller --repo-root . embedding sync \
  --batch-size 32 --max-retries 2 --max-nodes 10000
kgdistiller --repo-root . embedding sync \
  --profile primary --profile secondary
```

With no `--profile`, sync selects the effective machine-local
`embedding_profile`; repeated options explicitly select multiple policy
profiles. Sync calls providers only for eligible `missing` and `stale`
canonical inputs. A second unchanged sync performs zero document embedding
calls. The defaults limit a provider batch to 32 inputs, retry each batch at
most twice, and admit at most 10,000 missing or stale nodes; fixed internal caps also
bound total input bytes, vector bytes, and provider batch count.

Provider results are completely validated before an atomic index generation
is published. Existing ready vector bytes are left unchanged. Provider failure
publishes nothing, and a graph/snapshot change during provider work or before
the final status observation returns `stale-generation`. Query and MCP paths never
invoke document sync; a query-vector request is a separate read operation.

This status is not yet a portable-store readiness gate. Until that later gate
is implemented, `store snapshot` and `store verify` must not be described as
RAG-ready merely because they succeeded; inspect embedding coverage
separately.

## Planned hybrid retrieval

Search and context accept either one compatible legacy query or one bounded
`qlkg-retrieval-plan-v1` JSON file:

```sh
kgdistiller --repo-root . agent search "measure space" --limit 20
kgdistiller --repo-root . agent search \
  --plan knowledge/build/measure-space.plan.json
kgdistiller --repo-root . agent context \
  --plan knowledge/build/measure-space.plan.json --budget 6000
```

The plan is authoritative for namespace, identity/lexical/semantic
expressions, explicit graph seeds and edge types, filters, depth, strategy, and
limit. Planned mode rejects legacy retrieval flags rather than silently
overriding the plan. Legacy questions remain accepted through 4096 characters;
the adapter preserves the full question for context and deterministically
bounds each lane expression to the plan's 2048-character limit.

Search returns `qlkg-search-execution-v1`, including `plan_mode`, immutable
snapshot/graph digests, bounded identity resolutions, and a separately
validated `qlkg-search-result-v2`. Every lane exposes status, result count, and
a stable degradation reason. All semantic expressions share one query-only
batch and only scan current materialized vectors. They never trigger document
embedding or synchronization. Exact identities take precedence over score
fusion; unresolved ambiguity cannot be collapsed by an automatic retrieval
lane. Partial vector coverage remains usable but reports
`coverage-insufficient`; vectors bound to a different selected profile report
`profile-mismatch` before any query-provider call.

Query paths never create or refresh SQLite. A missing index produces
`index-unavailable`; a graph/index digest mismatch produces `stale-index`; a
publication during lane execution produces `stale-generation`. In each case
the graph, index bytes, generation marker, and portable store remain unchanged.
Run explicit `sync`, `embedding sync`, or verified `store materialize` as the
appropriate maintenance operation before retrying.

Context packing retains the bounded identity-resolution records. An ambiguous
candidate group is included atomically; if the token budget cannot contain the
whole group, the context omits the entire group with
`ambiguous-group-budget` instead of presenting one candidate as resolved. A
candidate set truncated at the 500-ID contract boundary is always omitted with
`ambiguous-group-overflow`, because its unknown tail cannot be packed safely.

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

`kg_search` and `kg_build_context` accept exactly one `query` or inline `plan`.
The server injects the selected provider profile internally; MCP arguments do
not accept base URLs, credential environment names, tokens, or arbitrary
provider configuration.

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
- `knowledge/sources.json`, `identities.json`, `alignments.json`, and the
  credential-free `embedding-policy.json`;
- every deterministic file under `knowledge/graph/`;
- `knowledge/documents.jsonl`, `knowledge/store.json`, and every file under
  `knowledge/embeddings/`;
- an explicit kgdistiller package version and product commit in deployment and
  export provenance.

Ignore `knowledge/build/`, SQLite and WAL files, transaction staging
directories, plans, receipts, local Agent snapshots, rendered pages,
credentials, query logs, and provider caches. Exact vectors under
`knowledge/embeddings/` are an intentional exception: they are portable,
content-addressed retrieval artifacts. They remain derived from the authority
graph and MUST NOT define identity or trusted relations.

SQLite rebuilds retain exact valid vector bytes for still-existing nodes so
status can classify stale inputs. Only rows whose canonical input digest still
matches are query-reusable; stale rows are dropped before snapshotting.

Ordinary Git is recommended for a small personal store. Individual vector
objects are bounded by their declared dimensions and verified by SHA-256. Do
not add Git LFS by default: it introduces another remote object store and makes
offline restoration depend on LFS checkout. Revisit that choice only after
measuring repository size and confirming LFS availability on every machine.

A Git remote or ordinary encrypted backup service can synchronize the store.
Avoid file-sync tools concurrently rewriting the same working tree while
`ingest apply` holds the local writer lock. A local snapshot is not a backup
until a commit exists elsewhere; report a remote as confirmed only after a
successful push of the containing commit.

Before changing machines:

```sh
kgdistiller --repo-root . store snapshot
kgdistiller --repo-root . store verify
git status --short
git bundle create ../notes-backup.bundle --all
```

The bundle is optional but provides one portable, verifiable Git backup. Keep
private authority data only in storage appropriate for that data.

## Restore drill

1. Clone the authority store or restore its Git bundle.
2. Install the recorded kgdistiller version and verify its product commit.
3. Run `kgdistiller store verify` before changing any tracked file.
4. Delete any restored `knowledge/build/` directory; it is disposable.
5. Run `kgdistiller store materialize`; it restores SQLite and exact vectors
   without provider calls.
6. Run `kgdistiller agent status` and confirm the store generation.
7. Resolve several known IDs and build one bounded context bundle.
8. Run `ingest plan` for a disposable reviewed request before allowing writes.

Do not run `sync` first merely to make a failed `check` disappear. A mismatch
between committed authority and graph is evidence to review.

## Transaction recovery

The writer journal lives under `knowledge/build/kgdistiller-ingest/`. On the
next write, kgdistiller detects an interrupted installation and restores the
backed-up authority, alignment, graph, registry, and disposable index before
validating another request. Preserve the journal and its backup directory when
diagnosing a crash.

`--database` is the documented logical kgdistiller index path. Public CLI, MCP,
and Python readers resolve its unique newest current marker and open the
selected generation read-only. Calling raw `sqlite3.connect(configured_path)`
is not the public current-generation interface and can observe only a legacy
canonical file after the logical index advances. Published physical generations
are immutable. Internal maintenance clones the selected database, applies the
bounded mutation to that private copy, and publishes a new bound generation;
it never updates the file selected by an existing marker in place.

On Windows native and WSL, the logical path has a sibling
`.knowledge.sqlite.generations/` reader-isolation sidecar. The journal and
status output retain the canonical logical spelling; existing readers finish
on their old physical generation, while later readers resolve the new marker.
Physical generation files are disposable local state and are excluded from
every portable artifact and portable store. Keep the sidecar with
`knowledge/build/` until readers and recovery have finished.

Choose one writer environment for each logical database and stop its writers
before switching environments. Windows native and WSL use separate host-local
locking APIs; their per-index locks are individually bounded, but they are not
claimed to mutually exclude a Windows process and a WSL process writing the
same sidecar at the same time.

If recovery returns `rollback-failed`, stop all writers, copy the complete
repository, and inspect the journal targets. Restore the recorded backups or a
known-good Git revision before retrying. Never delete the journal to force a
mixed generation to look committed.

## Upgrade procedure

1. Require a clean authority worktree and a passing `check`.
2. Read the release compatibility and migration notes.
3. Update the package pin on a branch.
4. Run `agent status`, the applicable product workflow, and `check` before any sync.
5. Run a transaction `plan` with current candidate/query digests.
6. Apply only after reviewing the plan and backing up the authority repository.
7. Commit the product-version record and any deterministic graph migration atomically.

Downgrades are safe only when the older release declares every committed schema
readable. Keep the pre-upgrade Git revision until a restore drill succeeds.
