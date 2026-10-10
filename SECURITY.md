# Security policy

## Supported release

Security fixes target the latest published minor release. Data-contract
changes are never delivered as an implicit security fix; they require an
explicit versioned schema and release boundary.

## Threat boundary

kgdistiller is a local, single-user engine. It validates bounded registered
paths, rejects traversal, symlinked and unsafe source and entry paths, refuses
to read the store while an ingest install is in progress, bounds MCP and query
inputs, and serializes transactional writers under one lock. MCP is read-only.
kgdistiller has no web server and no publishing surface, and it is not an
authenticated multi-user service.

Version 0.4 has no remote model provider, credential, database, or machine-
profile runtime. The optional embedding lane runs pinned local models and keeps
a rebuildable vector cache under `.knowledge/build/`. Sources are registered
UTF-8 text documents of any format; kgdistiller reads them as lines and never
parses, executes or converts them. Reviewed entries and accepted edges under
`.knowledge/` are the only knowledge; consistency with sources is checked by
comparing text, not by stored content hashes. The Obsidian plugin's graph feed
is derived; never register or rescan it as a source. The knowledge-project root
may be opened as an Obsidian editor vault without changing these boundaries.

The user-level vault registry is a machine-local locator, not authority. It
contains absolute local paths and therefore may disclose directory names; do
not commit, publish, or copy `~/.kgdistiller/vaults.json` as part of a knowledge
project. The portable `.knowledge/vault.json` contains only a schema discriminator
and random vault UUID. Registry resolution verifies that this UUID matches
before a command uses a registered path.

Knowledge projects may contain private data. Do not attach sources, entries,
edge files, transaction journals, receipts, Obsidian graph feeds, or agent
configuration to a public issue. Produce a minimal synthetic reproducer.

## Reporting

Report a suspected vulnerability privately to the repository owner before
public disclosure. Include the affected version, operating system, minimal
synthetic reproduction, impact, and whether an untrusted repository was
involved. Never include private source or entry text or a
live credential.
