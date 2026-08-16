# Native Vault deployment contract

## State boundaries

Keep these states distinct:

- the machine-local Vault registry locates Vault IDs and roots;
- `.kgdistiller/vault.json` defines portable Vault identity and authority roots;
- native Markdown and the source ledger are authority/evidence;
- `.kgdistiller/graph` is the exact derived `qlkg-v3` generation;
- `.kgdistiller/store.json` is the refreshable portable-store pointer;
- Git history/remotes are user-controlled transport state;
- packaged frontend/API service is loopback local state;
- legacy stores/static sites/Obsidian projections are isolated downstream state.

No status in one boundary proves another.

## Initialize, register, and move

`vault init` creates one new native layout and registers it. `vault add` accepts
an existing validated native Vault without changing its ID. Authority roots are
portable, pairwise non-overlapping under NFC/casefold comparison, and never
live under `.git`, `.obsidian`, or `.kgdistiller`.

To move or restore a Vault, snapshot and verify the old rollback source,
perform the user's chosen copy/move, run `vault verify NEW_PATH`, remove the old
registry entry, add the new root, then run `vault doctor` and `recall status`.
A same-ID old root is never replaced automatically. Registry removal and
addition create a deliberate non-atomic lookup interval.

## Portable store v3

`vault snapshot ID` captures one coherent generation under the Vault and
registry guards. In-place capture writes fixed missing scaffolds without
clobbering different bytes and publishes `.kgdistiller/store.json` last.
External capture stages a complete verified snapshot next to the destination
and installs it without replacement.

`vault verify TARGET` does not use the machine registry and performs no write.
It validates the closed manifest and self-digest, portable inventory, native
notes, current ledger/generation, referenced blobs, all durable receipts,
official graph hydration/source hashes, and byte-exact recompilation. A
snapshot-copy contains exactly its managed inventory apart from `.git`; an
in-place Vault may retain explicitly excluded local state.

Clone or copy, verify, then add. Do not use `knowledge sync` to hide a failed
store verification.

## Local workspace

Bare `kgdistiller serve` loads only packaged, self-verified frontend assets and
the versioned federated API, with API routing taking precedence over static
assets. It binds to loopback and is not an authenticated multi-user service.
`--legacy` selects the isolated legacy graph browser. Static files, source
excerpts, and API generations must never cross those modes.

## Explicit legacy store and exports

Only a user-selected marker project may use `--repo-root PROJECT check`,
`store snapshot`, and `store verify` with `qlkg-store-v2`. A static export then
requires explicit product/source provenance and its bundled standalone verifier.
An Obsidian export is a replaceable lossy projection and is never registered,
rescanned, or ingested. `serve --legacy` exposes only the old single-project
browser. None of these operations create or verify `qlkg-vault-store-v3`.

## Git and recovery

Git actions require explicit authority. Prefer private transport for personal
knowledge. Record a confirmed commit/remote only when actually observed.

Pending native ingest journals fail closed. Store verification never enters
recovery. Preserve third-party or uncertain states for diagnosis. Do not delete
build stages, receipts, or backups by prefix guessing.
