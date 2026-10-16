# Security policy

## Supported release

Security fixes target the latest published minor release. Changes to the record
format or the command output shapes are never delivered as an implicit security
fix; they require an explicit release boundary.

## Threat boundary

kgdistiller is a local, single-user engine. It validates bounded registered
paths, rejects traversal, symlinked knowledge trees and unsafe source and
record paths, and bounds MCP inputs and responses. Writers that change
knowledge or the registry (`accept`, `check --fix-lines`,
`base add|rm`, `embedding set|off`) serialize on the dedicated `$KGDISTILLER_HOME/lock` file; no
data file is ever locked or truncated, and `accept` replaces a record only with the replacement draft given to it.
MCP is read-only. kgdistiller has no web server and no publishing surface, and
it is not an authenticated multi-user service.

The core (`kgd` and its MCP server) has no remote model provider, credential,
network service or machine-profile runtime. With the `retrieval` extra
installed, `kgd embedding set` downloads the model's weights, configuration and
sentencepiece tokenizer from the Hugging Face Hub: no token is
sent (`token=False`), no remote code runs (the network is transformers' own
XLM-RoBERTa), and
`HF_HUB_OFFLINE=1` forbids network access once the weights are in the local
cache. Record text is encoded locally and never leaves the machine: the one
embedding process per user listens on a socket (a named pipe on Windows) in a
directory only that user can open, and every connection proves a key stored
there. Sources are
registered UTF-8 text documents of any format; kgdistiller reads them as lines
and never parses, executes or converts them. Records under each base's
`.knowledge/` are the only knowledge; consistency with sources is checked by
comparing text, not by stored content hashes.

The Obsidian plugin is desktop only. It reads knowledge and drafts only
through a resident `kgd mcp` and never parses records or opens the home or the
database itself. It runs `kgd base list`, `kgd sheet SOURCE --json`,
`kgd accept`, `kgd index`, `kgd index --no-embed` and `kgd embedding` with the
configured executable, and writes and deletes draft files; a draft becomes
knowledge only when the owner presses Accept. Every `kgd` run except
`kgd embedding set` gets `HF_HUB_OFFLINE=1`. The plugin's optional agent is
the only part that uses the network and credentials: it sends the selection,
the paragraph around it, the records it reads and its questions to the model
providers the owner configures, search queries to Exa's hosted MCP server, and
terms to Wikipedia and arXiv for Find sources. It requires HTTPS except for
loopback services, follows no redirects, and keeps API keys only in Obsidian
SecretStorage; the plugin's `data.json` stores only secret identifiers, and
keys are masked in displayed errors. Its hidden-folder indexing exposes `.knowledge/` to Obsidian search
and to the vault's other plugins. `kgd obsidian install` writes only
`<root>/.obsidian/plugins/kgdistiller/{main.js,manifest.json,styles.css,data.json}`
and `<root>/.obsidian/community-plugins.json` of a registered base (creating
`.obsidian/plugins/` when missing), writing each file through a temporary
sibling in the same directory that it removes again. It
refuses unregistered roots, a symlinked `.knowledge` tree and symlinked
destinations, and reads
`.obsidian/app.json` only to warn about its link format.

`$KGDISTILLER_HOME` (default `~/.knowledge`) is private owner data.
`config.json` and `types/` hold local base paths, source globs and user-defined
document types, and may disclose directory names. The derived database
`index.sqlite` (with its `-wal` and `-shm` files) contains the full text of
every indexed record, including evidence quotes from private sources, and the
vectors derived from that text: keep it in the private home, never copy it into
a base or a repository, and delete it freely, since `kgd index` rebuilds it.
`kgd index` is its only writer; search, resolve, get, neighbors, browse, pack
and the MCP server open it read-only. Keep `config.json` and `types/` in a
private local Git repository whose `.gitignore` excludes `index.sqlite*` and
`lock`; never commit them to a public repository or to this product
repository. A base is found only through
its registered root; there is no identity file inside the base.

Bases may contain private data. Do not attach sources, records, drafts, sheets,
the database, or agent configuration to a public issue. Produce a minimal
synthetic reproducer.

## Reporting

Report a suspected vulnerability privately to the repository owner before
public disclosure. Include the affected version, operating system, minimal
synthetic reproduction, impact, and whether an untrusted repository was
involved. Never include private source or record text or a live credential.
