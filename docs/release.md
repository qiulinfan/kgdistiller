# Public release policy

This document defines release gates. It does not authorize a push, package
publication, tag, GitHub release, or disclosure of personal knowledge.

## Data and output shapes

Version 1.0 has no named schema contracts. Knowledge lives in record files
whose format is specified in [model.md](model.md); the derived database and
every command's JSON output are specified in [model.md](model.md) and
[retrieval.md](retrieval.md) and asserted by the unit tests. MCP input
schemas are written inline in the server. A read never rewrites knowledge.

There is no older-format reader and no automatic migration. A personal
knowledge base is migrated once by its owner, outside the product. The
database is derived: a missing, damaged or other-version `index.sqlite` is
rebuilt by `kgd index`, never migrated.

## Release gates

License-notice checks normalize LF/CRLF line endings so native Windows Git
checkouts pass the same integrity check; missing or changed notice text still
fails. Verify that regression with `node --test scripts/license-notices.test.mjs`.

Run from a clean engine worktree. Root `npm run build` comes first: the
package and its editable build include the gitignored
`integrations/obsidian/main.js`, so every `uv run` and `uv build` in a fresh
checkout fails without it. The unit environment needs NumPy, which the `dev`
dependency group provides; the model itself is replaced by a fake encoder. The
int8 encoder tests (`tests/test_embedding_adapter.py`) need the `retrieval`
extra: they are skipped in CI, so run the suite once locally with an
interpreter that has it.

```sh
npm run build
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv build --out-dir build/release/2.0.2
uv run --locked python scripts/check_distribution.py --dist-root build/release/2.0.2
npm test
cd integrations/obsidian && npm ci && npm run check
```

The opt-in real-model smoke test is never run in CI. Run it with an
interpreter that has the `retrieval` extra and a model already in the Hugging
Face cache:

```sh
HF_HUB_OFFLINE=1 KGD_TEST_EMBEDDING_MODEL=BAAI/bge-m3 python -m unittest discover -s tests -p test_real_model.py
```

Then verify that:

- wheel and sdist contain the Python modules, every product Skill, both
  workflow manifests, the workflow guide and every `workflow_resources`
  document of both manifests, the `.codex/agents` and `.claude/agents`
  presets, the editable document-type starter templates, and the
  three-file Obsidian plugin bundle;
- an isolated environment installs the wheel and runs
  `scripts/smoke_installed_runtime.py` with `KGDISTILLER_HOME` and `HOME` set to
  temporary directories: `base add` creates the home and the base's
  `entries/`; a type with node and relation kinds and a source glob are
  registered; `source` and `accept` work on written records and a draft; `check` is clean, reports a shifted quote as moved, and
  `check --fix-lines` restores it; `index --no-embed` builds the database in
  the home and nowhere in the base; with `embedding` null, `search` and
  `search --no-dense` run only the lexical lane and the name lane, without
  NumPy or a model, rank the expected node first and find a CJK query;
  `resolve` and `get --source-lines` answer; `neighbors`, `browse` (bases, a
  base, a source file and `--kind`) and `pack` (records and gaps) answer;
  `obsidian install --base` installs the three plugin files, enables the plugin
  and sets `hiddenKnowledgeEnabled` in `data.json`; a rerun from the base root
  keeps the other `data.json` keys and warns once about a `relative`
  `newLinkFormat`; and an install from outside every base is refused;
- installed `kgdistiller`/`kgdistiller.exe` and `kgd` work on Linux, Windows and
  macOS;
- every test and the smoke script use a temporary `KGDISTILLER_HOME` and never
  read or write the real home;
- the index invariants hold: an incremental `kgd index` equals `--rebuild` and
  a build from a deleted database after edits, label cascades and an
  unparseable file, vectors included; read commands never create or write the
  database; a wrong `user_version` or a damaged file is recreated; no module
  imports `hashlib`;
- the embedding phase passes its fake-encoder tests: a text change sets `vec`
  to NULL, a record under another uid re-uses the vector, a model change
  invalidates every vector, a stale batch write is a no-op, `--rebuild` re-uses vectors by text
  without loading the model, batches hold 64 rows, over-length texts are
  listed under `truncated`, and every non-NULL `vec` encodes its row's text
  under `meta.embedding`; `index --no-embed` leaves changed rows unembedded;
- `search` runs the dense lane only when `meta.embedding` is set, encodes the
  query with `meta.embedding`, and, when stored vectors match the filters but
  the `retrieval` extra is missing, fails with an install hint unless
  `--no-dense` (MCP: `no_dense`) is given;
- `neighbors` returns dependency closures whose cycles end at the depth limit
  and whose records keep their minimum depth, the statement nodes and
  relations linking a record (`--dir in`) across relation-to-relation links,
  and examples by kind; `browse` answers every handle form and kind listing;
  `pack` keeps to its budget, reports over-budget records while later ones
  still fit, adds every record, relation or statement node alike, with role
  links to at least two packed records, ordered by packed targets, reports
  every gap reason, and its `bytes` equals the
  UTF-8 length of the compact JSON of its records; `resolve` separates a
  name's senses, the records whose label or alias has it, from mentions; and
  the MCP server exposes exactly the eight read-only tools;
- the embedding adapter takes a model id only and has no reranker, default
  model or revision pin; at most one embedding process runs per user;
- the home lock serializes `accept`, `relocate`, `check --fix-lines`, `base add|rm` and
  `embedding set|off`: a held lock makes each fail without writing; `accept`
  replaces a record only with the replacement draft it is given, and a refusal
  writes nothing; `relocate` moves a record or a draft whose passage lies in
  another base into that base with every link to it rewritten, and refuses
  when that base has the id or a replacement draft waits; and `--fix-lines`
  skips a file that changed during the run;
- sources of any extension are read as text, and no command parses source
  syntax or converts a source;
- `check` reports every error and every moved or stale record and exits 1 for
  any of them; staleness never hides a record from retrieval;
- the Obsidian plugin (version 2.0.3, `isDesktopOnly: true`) passes its
  vitest suites (the `kgd` client and its MCP session, the lookup model, the
  keyword refresh on record saves, capture, drafts, evidence, the agent, its
  judgment, providers, web search and sources, records with every entry of
  `case-folding.json`, hidden indexing, the manifest and the plugin
  lifecycle), type checking and a production bundle build; the
  `link-grammar.json` and `name-key.json` fixtures belong to the Python tests,
  which also check the plugin's `case-folding.json` against `unicodedata`; CI
  runs root `npm run build` and `npm test` on Linux, macOS and Windows;
- every materially updated Skill passes the active `skill-creator` validator,
  both product doctors, and an isolated Agent evaluation;
- POSIX and Windows copy/link doctor tests preserve unrelated Codex and Claude
  Code files;
- no credential, personal knowledge, source note, build artifact or private
  fixture is tracked in the product repository.

## Supply-chain checklist

Review the complete diff and status, version, changelog and license. Build from
a clean tagged commit into an empty distribution directory, inspect wheel and
sdist contents, and smoke-install the wheel in an isolated environment. Use
short-lived or trusted publishing credentials and never commit tokens. Tag only
after all gates pass; tag the Python core as `core-x.y.z` (for example
`core-2.0.2`), never a bare `x.y.z`, which is reserved for the Obsidian plugin
and starts its release workflow. Do not move a published tag. Keep the previous release
available. 1.0 data and APIs are intentionally incompatible with every 0.x
release, and 2.0 records and document types with 1.x: the owner migrates them
once, as the changelog's upgrade notes describe.

## Obsidian Community release entry

The monorepo publishes the plugin through root `manifest.json` and
`versions.json`, exactly mirrored from `integrations/obsidian`. The plugin is 2.0.3 and
the Python core is 2.0.2; they are versioned independently. A plugin release tag must
be the manifest's exact `x.y.z` version, without `v`; bare `x.y.z` tags are
reserved for plugin releases, and Python core tags are `core-x.y.z`.

```sh
npm ci
npm run build
npm test
node scripts/build-obsidian-plugin.mjs --verify-only --tag 2.0.3
```

The root build delegates installation and build to the integration, checks
that the bundle embeds the complete license and third-party notices, including
the Cytoscape.js, cytoscape-fcose, cose-base and layout-base copyright lines,
and copies ignored root `main.js` and `styles.css` for directory tooling. Python wheels and sdists and the `kgd obsidian install`
command consume `integrations/obsidian/{main.js,manifest.json,styles.css}`.
The version-checked release workflow builds from the tagged checkout, tests
metadata and the plugin, and publishes the three assets as a GitHub release. Do
not replace a published version's tag or assets; increment the plugin version
in both manifests, packages and compatibility maps before a new release.

Original project code uses MIT. The embedded MIT licenses of Cytoscape.js,
cytoscape-fcose, cose-base and layout-base, and the other upstream notices, must
remain present in the actual bundle. Obsidian review and listing are a separate
step after GitHub publication.
