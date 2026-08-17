# Deployment contract

## Portable authority boundary

The knowledge project owns registered Markdown, Typst, and LaTeX identity
authorities, `knowledge/derived/` Markdown evidence,
`knowledge/entries/` atomic authorities, reviewed source/identity/alignment
registries, the deterministic `kgdistiller-graph-v1` graph, canonical document inventory,
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
