# Local-first deployment and recovery

The knowledge project is the deployment and backup unit. It owns its registered
source documents, one Markdown file per accepted entry and one file of accepted
edges. The product checkout owns only engine code, schemas, the Obsidian plugin
bundle, Skills and workflow definitions. kgdistiller has no publishing surface;
a repository that publishes its notes owns its website and registries.

## Layout

```text
personal-knowledge-project/
├── notes/                         # registered source documents, any text format
└── .knowledge/
    ├── vault.json                 # stable vault identity
    ├── sources.json               # source registration; optional document types
    ├── entries/<id>.md            # one reviewed entry per knowledge node
    ├── edges.jsonl                # accepted semantic edges
    ├── .gitignore                 # ignores build/
    └── build/                     # rebuildable local work
```

`.knowledge/` is the only knowledge root; `kgdistiller init` creates
`sources.json`, `vault.json`, an empty `entries/`, an empty `edges.jsonl` and
`.gitignore`, and scans nothing. Entries and edges are the whole knowledge
state: each entry carries its id, label, kind, aliases, source path, line range,
understanding, human sections and verbatim Evidence quote. `build/` holds ingest
journals, plans, receipts, review drafts, the retrieval vector cache and the
Obsidian plugin's graph feed; all of it can be rebuilt or discarded.

A source registration holds `id`, `root`, `files` and an optional
`document_type`; the registry adds only optional `document_types` profiles, and
any other key is rejected. Sources are UTF-8 text documents of any format;
kgdistiller reads them as numbered lines and never parses or converts them.

A normal checkout is queryable directly: every command loads the entries and
edges into an in-memory view. There is no database, snapshot or materialization
step.

## Global command and machine-local registration

Install the package as a user-level tool on Windows, macOS, or Linux, then
register each knowledge repository once:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
uv tool update-shell
kgdistiller vault register PROJECT --name research
kgdistiller --vault research agent status
```

The portable `.knowledge/vault.json` stores the stable vault UUID and must travel
with the repository. The user-level `~/.kgdistiller/vaults.json` stores only
machine-local name/UUID/absolute-path mappings and the optional default. On
Windows, `~` is the current user's profile directory. Do not commit the
user-level registry, since its absolute paths are host-specific and may expose
local directory names. `KGDISTILLER_HOME` may select a different absolute
registry directory, while `KGDISTILLER_VAULT` selects a registered name or UUID.

Use `vault list`, `vault show NAME`, `vault default NAME`, `vault doctor`, and
`vault unregister NAME` to inspect and maintain the locator. Unregistering
never deletes the repository or its portable identity. A moved repository can
be registered at its new path by identity; `--replace` is required only when
the old path still exists, which prevents accidentally treating a copied vault
as a relocation.

## Check

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT check --fix-lines
```

`check` validates every entry and edge (file format, unique ids and names,
registered and readable sources, line ranges, kinds, edge endpoints and the
acyclic `prerequisite-for`) and compares each entry's Evidence quote with its
cited lines. It prints `OK: <n> entries, <m> edges` and exits 0, or lists every
error and every `moved`, `stale` or `ambiguous` entry, ends with
`FAILED: <x> errors, <y> stale entries`, and exits 1.

After editing a source so that cited lines shift, `check --fix-lines` rewrites
the line range of each moved entry (its quote found exactly once elsewhere in
the source) under the ingest writer lock and checks again. A stale entry (quote
gone) or an ambiguous one (quote found several times) needs a reviewed
re-capture. Staleness is reported only; it never hides an entry from retrieval
or the graph feed.

## Git synchronization

Initialize a private Git repository, commit, add a remote, or push only when
the user explicitly authorizes that action. Track:

- every registered source document and required authored asset;
- `.knowledge/vault.json` and `.knowledge/sources.json`;
- `.knowledge/entries/` and `.knowledge/edges.jsonl`;
- `.knowledge/.gitignore`.

Ignore `.knowledge/build/`, credentials and query logs. `check` proves local
consistency, not that a commit or remote synchronization happened.

After an ordinary clone or pull:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT agent resolve "KNOWN NAME"
```

If `check` fails on a clean checkout, restore a known-good revision or repair
the source on its owning machine. Never delete an interrupted ingest journal
under `.knowledge/build/kgdistiller-ingest/`; the next `ingest apply` recovers
it.

## Local services and the Obsidian graph feed

`kgdistiller mcp` exposes only bounded read-only operations.

Install the packaged read-only Obsidian plugin into a selected vault with
`kgdistiller --vault <name-or-id> obsidian install`; use `--replace` for an
upgrade. The installer manages only `main.js`, `manifest.json`, and `styles.css`,
preserves `data.json`, and can leave the enabled-plugin list untouched with
`--no-enable`. Open the knowledge repository root as the editor vault; its
registered sources and `.knowledge/entries/*.md` remain the knowledge.

The plugin reads one derived file, `.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`). Regenerate it with
`kgdistiller --repo-root PROJECT export obsidian` after an ingest, a
`check --fix-lines` or a source edit; the command writes it atomically from
every entry and every accepted edge. The plugin's typed graph view shows
concepts (by kind and understanding), source documents, semantic edges and
definition edges; its Open buttons open an entry's own file, or a source at its
cited lines, when the feed lies inside the project's `.knowledge/` tree in the
same vault. Never register the feed as a source or rescan it.

## Agent runtime integration

`kgdistiller codex link` and `kgdistiller claude link` install the Skills,
agent presets and product root declared by the workflow manifests; the matching
`doctor` command verifies them. Installed copies are product-owned: `doctor`
reports a copy that differs from the product source, and relinking replaces it
(and deletes retired copies), discarding any local edits to the installed
files. Change the product checkout, not the installed copy.

## Deployment receipt

Record the absolute project root, installed kgdistiller version and exact
product commit when known, the entry and edge counts from `agent status`, the
`check` result, and Git commit/remote state only when actually confirmed. Never
include full source or entry content, credentials, or unbounded excerpts.
