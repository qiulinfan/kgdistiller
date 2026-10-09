# Deployment contract

## Portable authority boundary

The knowledge project owns registered Markdown, Typst, and LaTeX identity
authorities, `knowledge/entries/` Markdown atomic authorities linked directly
to native evidence, existing `knowledge/derived/` evidence where used, reviewed
source/identity/alignment registries, the deterministic `kgdistiller-graph-v1` graph, canonical document inventory,
and `kgdistiller-store-v1` manifest. Opening that
project as an Obsidian vault does not change the authority boundary. The
product checkout, local browser state, static site, and generated Obsidian
projection directory are not authority or backup roots.

Keep `knowledge/build/`, journals, plans, receipts, credentials, query logs,
and generated projections local and ignored. Version 0.4 has no database,
embedding bundle, provider configuration, machine profile, or materialization
contract.

Version 0.4 has no legacy schema reader. Do not silently relabel or preserve an
older derived graph. First require a committed Git rollback point containing
native authorities and reviewed registries, then preserve any entries and edges
that need human re-review. Move the old generated `knowledge/graph/` outside
the project or delete that exact directory after confirming the rollback
commit. Write current registry discriminators, run an unscoped `sync` to derive
`kgdistiller-graph-v1`, and re-author reviewed metadata under
`kgdistiller-agent-delta-v1`.

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
registry/source fields. `node_kinds` is a nonempty list of unique user-defined
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

Before snapshot or export, run `check` and `agent status`. Run `store snapshot`
then `store verify`; for a separate snapshot, verify its output root. On restore,
verify before any query. A verified clone is directly queryable through the
generation-checked JSON `GraphView`.

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
