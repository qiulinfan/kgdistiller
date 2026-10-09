# Deployment contract

## Knowledge and optional snapshot boundary

The knowledge project owns native Markdown, Typst and LaTeX sources,
`knowledge/entries/` Markdown atomic entries linked to evidence, source
registration and durable graph records. Reviewed identities and alignments are
optional; preserve nonempty registries and create them only when used.
`kgdistiller-graph-v2` retains identity/alias/orphan state, accepted edges and
reference occurrences. Entry content is read from the bound Markdown, not a
second persisted JSONL body store. Existing public graph v1 is read-only
compatible; explicit writes emit v2 while preserving accepted state.

`documents.jsonl` and `store.json` package an explicitly requested portable
snapshot. They are not live canonical knowledge or prerequisites for capture,
query, export or ordinary Git cloning. Never generate a snapshot merely because
a checker expects one. Existing derived evidence is retained only where used.
Opening the knowledge project as an Obsidian vault changes none of these roles.
The product checkout and generated projections are not authority or backup roots.

Keep `knowledge/build/`, journals, plans, receipts, credentials and query logs
local and ignored. Exports are optional chosen consumer outputs; retain or
rebuild those still used by a site or local plugin. No database materialization
is required.

Pre-0.4 core graphs and SQLite artifacts remain unsupported. Preserve their
native authorities and reviewed metadata, recover with the earlier release
when needed, then rebuild and review under current contracts. Do not relabel
these artifacts or apply this recovery procedure to public graph v1 data.

## Source extraction profiles

The source registry may contain a `document_types` mapping. Names and node kinds
are supplied by the user; the product does not pre-register research, mathematics
or computing classes. For example, using placeholder values:

```json
{
  "document_types": {
    "USER_DOCUMENT_TYPE": {
      "node_kinds": ["USER_NODE_KIND"],
      "extraction_guidance": "The user's rules for nodes, relations, applications and pending gaps."
    }
  },
  "sources": [
    {
      "id": "SOURCE_REGISTRATION",
      "root": "SOURCE_DIRECTORY",
      "files": ["*.tex"],
      "document_type": "USER_DOCUMENT_TYPE"
    }
  ]
}
```

This is a fragment of `knowledge/sources.json`; preserve its schema and other
actual registry/source fields. Without extraction profiles, minimal registration
needs only `id`, `root` and `files`. Omit unused document types, fields, topics,
web settings and classification policies; do not invent a general field. `node_kinds` is a nonempty list of unique user-defined
names, and `extraction_guidance` contains the user's extraction rules. A source
selects a registered profile by exact name; a submitted semantic `kind` must
belong to that profile. Registration never reclassifies existing identities.
Its file extension selects the reader; fields/topics describe knowledge domain.
If different files need different profiles, register separate bounded file sets.
An omitted `document_type` leaves a source unclassified; do not silently assign
one. Read the original `.md`, `.typ` or `.tex` source instead of converting it.
Knowledge entries and def/pending link sheets remain Markdown.

`kgdistiller --repo-root PROJECT scan --file RELATIVE_AUTHORITY` exposes the
selected source's `document_type` in `sources`, plus the corresponding
`document_types` profile. It also works before definition markers are added.
This read-only route lets extraction workflows read the user's policy without
assuming graph nodes already exist. Registration alone creates no nodes and
makes no RAG or vector-index choice.

## Required checks

Before snapshot or export, run `check` and `agent status`. For a requested
snapshot, run `store snapshot` then `store verify`; for a separate snapshot,
verify its output root. Before restoring an actual existing snapshot, verify
it. An ordinary clone without a snapshot needs source/graph checks, not a newly
generated store. A valid graph is directly queryable through generation-checked
`GraphView`. Report an existing unrefreshed snapshot as stale until verified.

Never run `sync` to mask a verification mismatch and never hand-edit manifests,
invent digests, or delete an interrupted ingest journal. Restore a known-good
generation or repair the native authority on its owning machine.

## Product and publication provenance

Record installed kgdistiller version and full product commit when discoverable.
A static publication must be a `kgdistiller-static-export-v1` bundle produced by
`export site` and verified by its packaged dependency-free verifier. Its
receipt binds producer, clean source repository revision/digests, visibility
policy, private/public graph digests, and exact artifact bytes. Public edges
contain only the structural `source`, `relation`, and `target` triple.

Refreshing a managed static bundle requires `--replace`: verify the predecessor,
generate and verify a successor in staging, then use the rollback-safe swap.
Never pre-delete an adopted bundle.

The knowledge-project root may be the Obsidian editor vault; registered
Markdown files and `knowledge/entries/*.md` remain authority. An Obsidian export is a managed
`kgdistiller-obsidian-projection-v1` downstream subtree, or an external browsing-only
vault/projection. It is lossy, disposable, and never a source. Do not add its
root to the source registry or feed any projected note to scan, sync, candidate,
or ingest.

Installing, linking, snapshotting, exporting, committing, pushing, and making
data network-public are separate authorities. Never place private sources or
secrets in a product repository, receipt, command output, Codex configuration,
or public export.
