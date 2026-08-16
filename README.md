# kgdistiller

`kgdistiller` is a local-first, multi-Vault knowledge workspace for source-backed
research. A native Vault keeps ordinary Markdown concept, field, and topic notes
as authority; immutable source versions and derivation evidence explain where
knowledge came from; deterministic `qlkg-v3` JSON remains the derived graph.

The product has no database, vector store, embedding provider, CDN, analytics,
or required external/remote network service. Federated recall loads validated
generations from registered Vaults and returns stable `vault_id:node_id`
handles. The installed browser is a self-contained, unauthenticated loopback
HTTP workspace.

## Install

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
uv sync
uv run kgdistiller --help
```

Or install the command:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
```

Markdown is the native knowledge-note format. Markdown, Typst, and LaTeX can
all be captured as immutable source evidence.

## Create and register Vaults

Initialize a new Vault and inspect the machine-local registry:

```sh
kgdistiller vault init /path/to/analysis-vault \
  --id analysis --label "Analysis"
kgdistiller vault list
kgdistiller vault doctor analysis
```

Register an existing local native Vault without changing its portable ID:

```sh
kgdistiller vault add /path/to/existing-vault
kgdistiller vault doctor VAULT_ID
kgdistiller knowledge check --vault VAULT_ID
```

`vault add` validates its `vault.json`; a newly initialized/local Vault need not
have `store.json` yet. A copied portable snapshot instead follows the stricter
`vault verify PATH` then `vault add PATH` clone flow below.

The registry is a locator, not portable knowledge. `.kgdistiller/vault.json`
defines Vault identity and authority roots. A source path resolves without a
repository-root argument:

```sh
kgdistiller vault locate /path/to/analysis-vault/Sources/chapter.typ
```

Resolution fails closed when a path is missing, unregistered, excluded,
overlapping, or escapes through a link.

## Capture source evidence

Capture a registered Markdown, Typst, or LaTeX file before curating from it:

```sh
kgdistiller source status /path/to/analysis-vault/Sources/chapter.typ
kgdistiller source capture /path/to/analysis-vault/Sources/chapter.typ
kgdistiller source diff /path/to/analysis-vault/Sources/chapter.typ
```

Capture stores a content-addressed raw blob plus canonical document, version,
and derivation ledgers. Newline-only checkout changes carry forward reviewed
derivations; semantic changes become stale until reviewed. Capture alone never
creates or approves a concept.

## Query one federation

Use the public recall surface instead of opening graph shards:

```sh
kgdistiller recall status
kgdistiller recall roots --vault analysis
kgdistiller recall resolve "Measure space" "Sigma algebra"
kgdistiller recall search "measurable structure" \
  --vault analysis --scope analysis:measure-theory
kgdistiller recall get analysis:measure-space
kgdistiller recall expand analysis:measure-space --depth 2
kgdistiller recall context --handle analysis:measure-space --budget 6000
```

Exact names and collision-free reviewed aliases may establish identity.
Taxonomy, lexical, and graph lanes retrieve and rank candidates but never
invent identity. Reports preserve incomplete Vaults, ambiguity, lane reasons,
evidence, omissions, and generation tokens.

## Review and ingest knowledge

Native concept and taxonomy notes are ordinary Markdown. Agents locate and
capture selected source files, inspect predecessor diffs, resolve qualified
identities, and prepare one reviewed `qlkg-vault-ingest-request-v1`.

```sh
kgdistiller knowledge ingest plan request.json --output plan.json
kgdistiller knowledge ingest apply request.json --receipt receipt.json
kgdistiller knowledge check --vault analysis
```

Plan changes no live bytes. Apply rechecks the registry, Vault, source ledger,
live source, notes, recall report, and graph generation, then atomically installs
native notes, derivation rows, a deterministic graph generation, and a durable
content-addressed receipt. See
[docs/transactional-ingest.md](docs/transactional-ingest.md).

## Open the workspace

```sh
kgdistiller serve
```

Bare `serve` starts the packaged multi-Vault workspace and `/api/v1` on
<http://127.0.0.1:8765/> by default. It works from any current directory and
loads no CDN resource. It is a loopback personal service, not an authenticated
multi-user server. The explicit `kgdistiller serve --legacy` mode is isolated
for the old single-project graph browser.

## Portable Vault store

Refresh an in-place `qlkg-vault-store-v3` pointer or create a no-clobber copy:

```sh
kgdistiller vault snapshot analysis
kgdistiller vault snapshot analysis --output /path/to/analysis-copy
kgdistiller vault verify /path/to/analysis-copy
```

The store binds native notes, the current source ledger/generation, referenced
raw blobs, every durable receipt, the exact derived graph, and fixed scaffolds.
It excludes machine registry state, Git/Obsidian settings, build journals and
caches, old source generations, unreferenced blobs, and the legacy store.

Clone or copy, verify, then add:

```sh
kgdistiller vault verify /path/to/cloned-vault
kgdistiller vault add /path/to/cloned-vault
kgdistiller recall status --vault analysis
```

Moving a registered Vault is explicit: snapshot and verify the old rollback
copy, perform the user-controlled copy or move, run `vault verify NEW_PATH`,
then remove the old registry entry, add the new root, and run `vault doctor`
and `recall status`. The registry transition is not an atomic filesystem move. See
[docs/deployment.md](docs/deployment.md).

## Obsidian

Open a native Vault itself in Obsidian. Concept, field, and topic notes remain
ordinary visible Markdown authority; Obsidian settings are unmanaged. The
native graph keeps typed relations and multi-parent taxonomy, while Obsidian's
built-in graph shows links without relation types. See
[docs/obsidian.md](docs/obsidian.md).

`export obsidian` remains a lossy downstream projection only for explicitly
selected legacy marker projects or external browsing copies. Never register or
ingest that projection.

## Legacy migration

The old single-project marker pipeline (`--[[...]]--`, `#kn[...]`, `\kn{...}`),
`qlkg-ingest-request-v2`, `qlkg-store-v2`, static export, and Obsidian projection
keep their existing meanings behind explicit legacy commands. They are not
native Vault inputs and are never relabeled.

For adoption, first commit a Git rollback point and export any irreplaceable
curation with the old release. Prefer a new sibling native Vault, especially on
case-insensitive filesystems where `knowledge/` and `Knowledge/` collide. Copy
only user-approved source evidence into it, capture those sources, generate
reviewable native note candidates from the legacy active nodes, resolve every
identity through federated recall, and require a reviewed native transaction.
Then run `knowledge check`, `vault snapshot`, and `vault verify`. Re-distillation
is preferable when no irreplaceable legacy metadata exists.

## Product Skills and development

Install the eight shipped Skills, four agent presets, and workflow manifest:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

The native and isolated legacy workflow boundaries are documented in
[docs/product-workflows.md](docs/product-workflows.md). Data, deployment,
release, and measurement contracts are in
[docs/graph-contract.md](docs/graph-contract.md),
[docs/deployment.md](docs/deployment.md),
[docs/release.md](docs/release.md), and
[docs/performance.md](docs/performance.md).
