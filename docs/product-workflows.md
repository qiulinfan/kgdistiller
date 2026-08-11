# kgdistiller product workflows

kgdistiller owns the deterministic engine, CLI and read-only MCP server, all
knowledge and paper Skills, Codex agent presets, and the workflow contract in
`workflows/manifest.json`. A knowledge repository owns only its authorities,
registries, graph/store generations, local profile, and adopted static export.

The manifest is the portable authority for product asset discovery. It lists
every shipped Skill, every `.codex/agents` role preset, and the ordered steps
that compose the supported workflows. `kgdistiller codex doctor --source-only`
validates that the paths are portable, each Skill is self-contained, all Skill
metadata is present, agent presets have the required fields, and every asset is
used by a workflow.

## Install the Codex product assets

From a source checkout or installed package:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

The default `auto` mode is a live installation. It uses symbolic links on
POSIX. On Windows it first tries symbolic links, then uses directory junctions
for Skills and the canonical product root and same-volume hardlinks for agent
files. It never silently degrades to a snapshot. Use `--mode symlink` to
require symbolic links, or explicitly choose `--mode copy` for a non-live
snapshot that must be refreshed after product changes. `doctor` reports
`real_time` and fails when a managed copy has become stale. It also fails
closed when an editor atomically replaces the source inode behind a managed
hardlink; rerun `link` to establish the new owned hardlink. Repair is allowed
only when the detached target still hashes to the install digest in managed
state. The same receipt permits a retired hardlink to be removed after its
source is renamed; a digest mismatch remains a wrong-owner failure.

The portable workflow entry is
`$CODEX_HOME/workflow-products/kgdistiller/workflows/manifest.json`; resolve its
`workflow_guide` relative to the adjacent canonical product root. Agent
presets read that entry before selecting workflow steps, so they work from any
knowledge-project current directory. `--codex-home PATH` selects an isolated
Codex home for testing.

The linker manages only the Skill names in the product manifest, agent files
named `kgdistiller-*.toml`, the namespaced
`$CODEX_HOME/workflow-products/kgdistiller` root, and
`$CODEX_HOME/.kgdistiller-product-links.json`. The manifest is the only asset
inventory. A later link transaction removes retired or renamed assets only
when that state file still proves ownership; modified copies, replaced links,
and detached hardlinks fail closed. The transaction stages replacements,
backs up current owned paths, and rolls back before publishing new state if an
install fails. It never writes or replaces global `AGENTS.md`, `config.toml`,
unrelated Skills, or unrelated agents.

The canonical Codex home and product root may not overlap in either direction,
so a user-home ancestor can never be mistaken for `$CODEX_HOME`. Managed state
must be one ordinary, non-reparse, single-link file, and every recorded source
must still belong to the active product root and manifest namespace before a
target can be refreshed or retired. Before any mkdir or state publication, the
linker rejects a symlink, Junction, or other reparse parent at the selected
Codex home and its `skills`, `agents`, `workflow-products`, and recovery
namespaces. Publishing state is the commit point. A later cleanup failure
returns `committed: true`, `cleanup_status: pending`, warnings, and exact
managed recovery paths; the next `link` validates the receipt and finishes
cleanup before making another change.

POSIX and PowerShell wrappers are available as
`scripts/link-codex-product.sh` and `scripts/link-codex-product.ps1`; both call
the same CLI boundary.

## Supported workflows

### Curate registered notes

Use `$curate-kgdistiller-notes` to bound and extract authorities,
`$query-kgdistiller` to resolve the full candidate batch, and
`$ingest-kgdistiller` to plan and apply one reviewed transaction. Identity
uncertainty and conflicts stop their own write path.

### Federate a paper

Use `$extract-paper-markdown` to build a complete traceable package, then
`$distill-paper-knowledge` to build and validate an isolated candidate
snapshot. `$query-kgdistiller` aligns it to the personal graph without
importing it. `$trace-concept-lineage` is an optional, separate learning-map
workflow that performs cited concept research.

### Import selected paper knowledge

Use `$import-paper-knowledge` only after the user names exact candidates and a
registered research authority. Revalidate those identities, then pass one
bounded reviewed request to `$ingest-kgdistiller`. Reading, summarizing,
distilling, or tracing a paper never implies import authorization.

### Publish a static site bundle

After project checks and store verification pass, run:

```sh
kgdistiller --repo-root PROJECT export site \
  --output knowledge/export/site \
  --product-commit FULL_PRODUCT_COMMIT \
  --source-repository https://example.invalid/owner/knowledge
python knowledge/export/site/verify_export.py knowledge/export/site
```

The producer commit is a version assertion, not free-form receipt text. When
the command runs from a product source checkout, every tracked and untracked
file must be clean and the explicit value must equal `HEAD`. When installed
`direct_url.json` exposes a VCS commit, an explicit value must equal that
commit. A full explicit commit is accepted only when the installation cannot
discover VCS provenance.

The knowledge instance is independently fail-closed. The exporter requires a
clean Git checkout including untracked files, verifies every authority against
the graph's `source_hashes`, and requires the source registry, the four core
private graph projections, every manifest-declared entry shard, and every
hashed authority to be tracked by the current `HEAD`. Authority hashes are
SHA-256 over UTF-8 text after universal-newline normalization (CRLF/CR to LF),
the same boundary used by scan, sync, ingest, stores, and `check`. Manifest
`source.revision` is that proven current commit; callers cannot override it.
The authority graph must also retain its full generation revision, and
`--source-repository` is required.

This deliberately makes adoption a two-commit process. First commit all
instance architecture, authorities, registries, graph artifacts, and (for a
refresh) the currently adopted bundle; call that clean commit A. Run `export
site` from A and verify the result. Then commit the exact new four-file bundle
as adoption commit B. The receipt names A while B records which verified bytes
were adopted. There is no circular attempt to predict B and no way to label
dirty instance bytes as the previous `HEAD`.

For a periodic refresh, add `--replace`. The command first verifies that the
destination is exactly an existing four-file kgdistiller bundle, generates and
verifies the successor beside it, then swaps directories with rollback. It
never asks callers to delete the old export first. A failed build or verifier
run leaves the prior bytes intact; a successful successor records the prior
`export_sha256` as `replaces_export_sha256`. The successful directory swap is
the commit point. Post-commit predecessor cleanup uses an ignored managed path
below `knowledge/build/.kgd-export-recovery/`; if cleanup fails, the command
reports the committed result with `cleanup_status: pending`, and the next
export verifies and completes that cleanup before continuing.

The bundle contains `manifest.json`, `graph.json`,
`knowledge-registry.typ`, and `verify_export.py`. The manifest records producer
repository/version/commit, source repository/revision/digests and published
source hashes, private/public graph digests and counts, visibility policy, and
artifact hashes/byte counts. Bundle artifacts are UTF-8 text; their `sha256`
and `bytes` fields use canonical LF text bytes, so the standalone verifier has
the same result after a Git CRLF checkout. V1 contains exactly three unique
artifact kind/path records for those non-manifest files. `graph.json` is already hydrated and
filtered by explicit `publish: true`, including its `diagnostics.errors`,
`diagnostics.warnings`, and `diagnostics.info` arrays. Consumers validate and
adopt these bytes; they do not install kgdistiller, read private graph files,
or recompute diagnostics. Public edges are a strict structural triple with
exactly `source`, `relation`, and `target`. Because the private graph does not
carry a separately publishable edge-authority contract, export drops edge
`evidence`, fingerprints, origin, confidence, curation metadata, and every
other source-derived field even when both endpoints are public.
