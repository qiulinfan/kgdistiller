# Public release policy

This document defines release gates. It does not authorize a push, package
publication, tag, GitHub release, or disclosure of personal knowledge.

## Data and output shapes

Version 0.4 has no named schema contracts. Knowledge lives in record files
whose format is specified in [model.md](model.md); the derived database and
every command's JSON output are specified in [model.md](model.md) and
[retrieval.md](retrieval.md) and asserted by the unit tests. MCP input
schemas are written inline in the server. A read never rewrites knowledge.

There is no older-format reader and no automatic migration. A personal
knowledge base is migrated once by its owner, outside the product. The
database is derived: a missing, damaged or other-version `index.sqlite` is
rebuilt by `kgd index`, never migrated.

## Release gates

Run from a clean engine worktree:

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
npm run build
uv build --out-dir build/release/0.4.0
uv run --locked python scripts/check_distribution.py --dist-root build/release/0.4.0
cd integrations/obsidian && npm ci && npm run check
```

Then verify that:

- wheel and sdist contain the Python modules, every product Skill, both
  workflow manifests, the workflow guide and every `workflow_resources`
  document of both manifests, the `.codex/agents` and `.claude/agents`
  presets, the linkers, and the three-file Obsidian plugin bundle;
- an isolated environment installs the wheel and runs
  `scripts/smoke_installed_runtime.py` with `KGDISTILLER_HOME` and `HOME` set to
  temporary directories: `base add` creates the home and the base's
  `entries/`; a type with node and relation kinds and a source glob are
  registered; `sheet --json`, `sheet` and `accept` work on written records and
  a draft; `check` is clean, reports a shifted quote as moved, and
  `check --fix-lines` restores it; `index --no-embed` builds the database in
  the home and nowhere in the base; `search` ranks the expected node first and
  finds a CJK query; `resolve` and `get --source-lines` answer; the plugin
  installs into the vault;
- installed `kgdistiller`/`kgdistiller.exe` and `kgd` work on Linux, Windows and
  macOS;
- every test and the smoke script use a temporary `KGDISTILLER_HOME` and never
  read or write the real home;
- the index invariants hold: an incremental `kgd index` equals `--rebuild` and
  a build from a deleted database after edits, label cascades and an
  unparseable file; read commands never create or write the database; a
  wrong `user_version` or a damaged file is recreated; no module imports
  `hashlib`;
- the home lock serializes `accept`, `harvest`, `check --fix-lines` and
  `base add|rm`: a held lock makes each fail without writing, `accept` never
  overwrites a record and refuses without writing anything, and
  `--fix-lines` skips a file that changed during the run;
- sources of any extension are read as text, and no command parses source
  syntax or converts a source;
- `check` reports every error and every moved or stale record and exits 1 for
  any of them; staleness never hides a record from retrieval;
- the Obsidian plugin (version 0.1.5) passes its vitest suites, including the
  link-grammar parity fixture shared with the Python tests, type checking and
  a production bundle build;
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
after all gates pass; do not move a published tag. Keep the previous release
available, while treating 0.4 data and API changes as intentionally
incompatible.

## Obsidian Community release entry

The monorepo publishes the plugin through root `manifest.json` and
`versions.json`, exactly mirrored from `integrations/obsidian`. Plugin version
0.1.5 and Python core version 0.4.0 are independent. A plugin release tag must
be the manifest's exact `x.y.z` version, without `v`.

```sh
npm ci
npm run build
npm test
node scripts/build-obsidian-plugin.mjs --verify-only --tag 0.1.5
```

The root build delegates installation and build to the integration, checks the
embedded Cytoscape notice, and copies ignored root `main.js` and `styles.css`
for directory tooling. Python wheels and sdists and the `kgd obsidian install`
command consume `integrations/obsidian/{main.js,manifest.json,styles.css}`.
The version-checked release workflow builds from the tagged checkout, tests
metadata and the plugin, and publishes the three assets as a GitHub release. Do
not replace a published version's tag or assets; increment the plugin version
in both manifests, packages and compatibility maps before a new release.

Original project code uses MIT. The embedded Cytoscape.js MIT license and other
upstream notices must remain present in the actual bundle. Obsidian review and
listing are a separate step after GitHub publication.
