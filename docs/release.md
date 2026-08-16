# Release and compatibility policy

This document defines product gates. It does not authorize a commit, push,
package publication, tag, GitHub release, or disclosure of personal knowledge.

## Native contract matrix

| Contract | Role |
| --- | --- |
| `qlkg-vault-registry-v1` | Machine-local bounded Vault locator. |
| `qlkg-vault-v1` | Portable Vault identity and authority roots. |
| `qlkg-source-ledger-v1` | Current immutable source-generation manifest. |
| `qlkg-source-document-v1` | Stable source document identity/status. |
| `qlkg-source-version-v1` | Append-only captured source version. |
| `qlkg-derivation-v1` | Review/evidence state for one source version. |
| `qlkg-vault-ingest-request-v1` | Reviewed native note/ledger/graph transaction. |
| `qlkg-vault-ingest-plan-v1` | Staged review result, never a receipt. |
| `qlkg-vault-ingest-receipt-v1` | Durable content-addressed committed receipt. |
| `qlkg-vault-ingest-report-v1` | Closed native plan/apply success envelope. |
| `qlkg-vault-ingest-error-v1` | Stable path-free native failure envelope. |
| `qlkg-knowledge-report-v1` | Native sync/check result. |
| `qlkg-recall-request-v1` | Closed federated read request. |
| `qlkg-recall-report-v1` | Generation-bound qualified recall result. |
| `qlkg-recall-error-v1` | Stable path-free recall failure. |
| `qlkg-vault-store-v3` | Portable native Vault generation. |
| `qlkg-vault-store-report-v1` | Snapshot/verify result. |
| `qlkg-api-response-v1` | Closed `/api/v1` response envelope. |
| `qlkg-api-error-v1` | Closed versioned API error envelope. |
| `qlkg-frontend-bundle-v1` | Self-digested packaged frontend inventory. |
| `qlkg-v3` | Exact derived authority graph, unchanged. |

Published discriminators are immutable. A changed required field, identity
meaning, digest formula, ordering rule, or invariant requires a new schema
version. Readers fail closed on an unknown/incompatible schema.

## Legacy contracts remain isolated

`qlkg-ingest-request-v2`, `qlkg-ingest-receipt-v2`, `qlkg-store-v2`,
`qlkg-static-export-v2`, `qlkg-obsidian-projection-v1`, marker scanning, and
legacy query/execution contracts keep their existing meanings. This release
does not reinterpret or relabel them.

Native commands use `vault`, `source`, `knowledge`, and `recall`. Bare `serve`
starts the native packaged workspace. Legacy single-project commands,
`store`, `export site`, `export obsidian`, and `serve --legacy` require explicit
selection. A legacy report/digest/handle is never accepted as a native request
binding.

## Migration policy

There is no automatic conversion of marker authority, a legacy graph, Agent
delta, v2 store, static bundle, or Obsidian projection.

1. Commit legacy native authorities and reviewed registries as an exact Git
   rollback point.
2. With the compatible old release, export irreplaceable curated entries and
   semantic edges for human review.
3. Prefer a new sibling native Vault, especially where `knowledge` and
   `Knowledge` collide under filesystem case rules.
4. Copy only approved source evidence into the native Vault and capture it.
5. Generate reviewable native concept/taxonomy note candidates from legacy
   active nodes; do not copy graph JSON into `.kgdistiller/graph`.
6. Resolve every reuse/update through current qualified federated recall.
7. Plan/apply one reviewed `qlkg-vault-ingest-request-v1`.
8. Run native `knowledge check`, `vault snapshot`, and `vault verify`.

If no irreplaceable legacy curation exists, re-distillation is the preferred
migration. Keep the prior release available for legacy recovery.

## Feature-slice gate

F9 runs only the exact product surface:

- active `skill-creator` validation for the six materially changed Skills;
- source-only and installed product doctor/link tests;
- `tests.test_codex_product`;
- the disposable multi-Vault smoke test;
- packaging-content checks owned by the integrated release gate;
- Python 3.9 syntax, lock, and scoped diff validation.

Do not treat this slice gate as the final release gate.

## Integrated release gate

From a clean integrated worktree, the coordinator runs:

```sh
uv run python -m unittest discover -s tests -v
uv build --out-dir build/release/multivault-v1
uv run python scripts/check_distribution.py \
  --dist-root build/release/multivault-v1
```

Then install the wheel in an isolated environment and exercise two disposable
Vaults: init/add/locate, source capture and predecessor diff, native ingest,
federated recall/context, every `/api/v1` route, packaged frontend, portable
snapshot/verify/clone, move remove/add repair, and explicit missing-Vault
reporting.

## Distribution inventory

The wheel and sdist must contain:

- Python modules and every current JSON Schema;
- nested packaged static v1 assets plus exact frontend bundle manifest;
- all eight product Skills and metadata, four agent presets, v3 workflow
  manifest/guide, and both linkers;
- product/deployment/Obsidian/transaction/graph/release/performance docs;

The sdist additionally contains the frontend source/config/tests/scripts/lock
needed to reproduce packaged assets and the disposable multi-Vault smoke
script. The runtime wheel excludes those developer sources. Neither archive
contains `node_modules`, frontend `dist`, source maps, or remote dependencies.

No distribution may include a personal Vault, machine registry, credentials,
generated source blob, receipt, graph/store, `.obsidian`, build journal/cache,
frontend dependency tree, or untracked release fixture.

## Frontend and service gates

Build frontend assets twice from independent clean trees and require exact
bundle equality. Verify the closed manifest, self-digest, path/casefold
inventory, local-reference closure, offline URL policy, media/cache policy, and
absence of maps. The Python provider must load through package resources (also
from a wheel/zip), expose only `/` plus manifest-declared fingerprinted assets,
and give `/api/v1` routing precedence.

Bare `kgdistiller serve` must work from an arbitrary current directory, bind
loopback, use closed API/security/framing limits, and preserve explicit legacy
server bytes under `--legacy`.

## Supply chain

Review the exact diff/status, version, schemas, frontend lock, generated bundle,
license, and package inventory. Build from a clean verified commit into an empty
directory, smoke-install without source-tree access, and record exact commands,
counts, skips, hashes, commit, and remote branch. Use trusted/short-lived
publication credentials and never commit tokens. Tag only after every gate
passes; never move a published tag.
