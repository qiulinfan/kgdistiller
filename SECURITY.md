# Security policy

## Supported release

Security fixes target the latest published minor release. Data migrations are
never delivered as an implicit security fix; they require the normal explicit
migration contract.

## Threat boundary

kgdistiller is a local, single-user engine. It validates bounded registered
paths, rejects traversal and unsafe entry shards, binds the browser to
`127.0.0.1` by default, bounds MCP messages and query inputs, quotes FTS terms,
and serializes transactional writers. MCP is read-only. The built-in browser is
not an authenticated multi-user service.

Authority repositories may contain private data. Do not attach them, graph
snapshots, transaction journals, receipts, SQLite files, provider settings, or
credentials to a public issue. Produce a minimal synthetic reproducer.

## Reporting

Report a suspected vulnerability privately to the repository owner before
public disclosure. Include the affected version, operating system, minimal
synthetic reproduction, impact, and whether a non-loopback service or untrusted
repository was involved. Never include a live token or private authority text.

