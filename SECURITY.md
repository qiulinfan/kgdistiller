# Security policy

## Supported release

Security fixes target the latest published minor release. Changes to the record
format or the command output shapes are never delivered as an implicit security
fix; they require an explicit release boundary.

## Threat boundary

kgdistiller is a local, single-user engine. It validates bounded registered
paths, rejects traversal, symlinked knowledge trees and unsafe source and
record paths, and bounds MCP inputs and responses. Writers that change
knowledge or the registry (`accept`, `harvest`, `check --fix-lines`,
`base add|rm`) serialize on the dedicated `$KGDISTILLER_HOME/lock` file; no
data file is ever locked or truncated, and `accept` never overwrites a record.
MCP is read-only. kgdistiller has no web server and no publishing surface, and
it is not an authenticated multi-user service.

kgdistiller has no remote model provider, credential, network service or
machine-profile runtime. With the `retrieval` extra installed and `embedding`
set, sentence-transformers may download model weights from the Hugging Face Hub
on first use: no token is sent (`token=False`), `trust_remote_code` is off, and
`HF_HUB_OFFLINE=1` forbids network access once the weights are in the local
cache. Record text is encoded locally and never leaves the machine. Sources are
registered UTF-8 text documents of any format; kgdistiller reads them as lines
and never parses, executes or converts them. Records under each base's
`.knowledge/` are the only knowledge; consistency with sources is checked by
comparing text, not by stored content hashes. The Obsidian plugin reads record
frontmatter from the vault's metadata cache and writes no knowledge.

`$KGDISTILLER_HOME` (default `~/.knowledge`) is private owner data.
`config.json` and `types/` hold local base paths, source globs and user-defined
document types, and may disclose directory names. The derived database
`index.sqlite` (with its `-wal` and `-shm` files) contains the full text of
every indexed record, including evidence quotes from private sources, and the
vectors derived from that text: keep it in the private home, never copy it into
a base or a repository, and delete it freely, since `kgd index` rebuilds it.
`kgd index` is its only writer; search, resolve, get and the MCP server open it
read-only. Keep `config.json` and `types/` in a private local Git repository
whose `.gitignore` excludes `index.sqlite*` and `lock`; never commit them to a
public repository or to this product repository. A base is found only through
its registered root; there is no identity file inside the base.

Bases may contain private data. Do not attach sources, records, drafts, sheets,
the database, or agent configuration to a public issue. Produce a minimal
synthetic reproducer.

## Reporting

Report a suspected vulnerability privately to the repository owner before
public disclosure. Include the affected version, operating system, minimal
synthetic reproduction, impact, and whether an untrusted repository was
involved. Never include private source or record text or a live credential.
