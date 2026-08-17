# Security policy

## Supported release

Security fixes target the latest published minor release. Data-contract
changes are never delivered as an implicit security fix; they require an
explicit versioned schema and release boundary.

## Threat boundary

kgdistiller is a local, single-user engine. It validates bounded registered
paths, rejects traversal and unsafe graph entry shards, checks one complete
JSON generation before exposing a `GraphView`, bounds MCP and query inputs, and
serializes transactional writers. MCP is read-only. The packaged browser binds
to `127.0.0.1` by default, rejects misdirected `Host`/`Origin` values, serves
only packaged asset names, and binds every source excerpt to the graph snapshot
and authority hash that selected it. It is still not an authenticated
multi-user service.

Version 0.4 has no model-provider, credential, vector, database, or machine-
profile runtime. Native Markdown, Typst, and LaTeX authorities remain the only
knowledge source. Static-site and Obsidian outputs are downstream projections;
never register or rescan them as authority. The knowledge-project root may be
opened as an Obsidian editor vault without changing the authority boundary;
registered Markdown files there remain native authority. Only the managed
Obsidian subtree (or an external browsing-only output) is lossy and may omit
source evidence or format semantics.

The user-level vault registry is a machine-local locator, not authority. It
contains absolute local paths and therefore may disclose directory names; do
not commit, publish, or copy `~/.kgdistiller/vaults.json` as part of a portable
store. The portable `knowledge/vault.json` contains only a schema discriminator
and random vault UUID. Registry resolution verifies that this UUID matches
before a command uses a registered path.

Authority repositories may contain private data. Do not attach authorities,
private graph snapshots, transaction journals, receipts, portable stores,
static exports, Obsidian projections, or Codex configuration to a public issue.
Produce a minimal synthetic reproducer. Treat an explicitly configured
non-loopback browser host and any publication/export destination as separate
security decisions.

## Reporting

Report a suspected vulnerability privately to the repository owner before
public disclosure. Include the affected version, operating system, minimal
synthetic reproduction, impact, and whether an untrusted repository or
non-loopback service was involved. Never include private authority text or a
live credential.
