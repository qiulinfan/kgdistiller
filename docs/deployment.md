# Local-first deployment and recovery

kgdistiller has one global home and any number of bases. The home,
`$KGDISTILLER_HOME` (default `~/.knowledge`), registers every base, maps each
base's source documents to user-defined document types, and holds those types.
A base is a directory that owns its source documents, one Markdown file per
accepted entry and one file of accepted edges; it is the backup unit of its
knowledge. The product checkout owns only engine code, schemas, the Obsidian
plugin bundle, Skills and workflow definitions. kgdistiller has no publishing
surface; a repository that publishes its notes owns its website and registries.

## Layout

```text
$KGDISTILLER_HOME/                 # default ~/.knowledge
├── config.json                    # bases, their source globs, embedding
├── types/<name>.md                # one user-defined document type per file
├── .gitignore                     # "index.sqlite*" and "lock"
└── lock                           # writer lock; its contents are meaningless

BASE_ROOT/
├── notes/                         # source documents, any text format
└── .knowledge/
    ├── entries/<id>.md            # one reviewed entry per knowledge node
    ├── edges.jsonl                # accepted semantic edges
    └── build/                     # rebuildable local work
```

`.knowledge/` is a base's only knowledge root. Entries and edges are the whole
knowledge state: each entry carries its id, label, kind, aliases, source path,
line range, understanding, human sections and verbatim Evidence quote. `build/`
holds ingest journals, plans, receipts, review drafts, the retrieval vector
cache and the Obsidian plugin's graph feed; all of it can be rebuilt or
discarded.

A normal checkout is queryable directly: every command loads the entries and
edges into an in-memory view. There is no database, snapshot or materialization
step.

## Global command and base registration

Install the package as a user-level tool on Windows, macOS, or Linux, then
register each knowledge directory once:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
uv tool update-shell
kgd base add BASE_ROOT --name research
kgd agent status --base research
```

`kgd base add PATH [--name N]` creates the home on first use (`config.json` as
`{"bases": {}, "embedding": null}`, an empty `types/` and the `.gitignore`),
registers the directory and creates `PATH/.knowledge/entries/`. The default
name is the directory's basename; a name must match `^[a-z0-9][a-z0-9-]*$`, so
pass `--name` otherwise. `kgd base list` prints every base with its stored path,
resolved root, availability and entry count; `kgd base rm NAME` removes only
the registration and never touches the directory. `base add` and `base rm`
hold the home lock and write `config.json` atomically. All three print JSON.

`KGDISTILLER_HOME` is the only environment variable for kgdistiller's own home
and data (the runtime linkers `codex|claude link|doctor` also honor `CODEX_HOME`
and `CLAUDE_CONFIG_DIR`); it must be absolute after `~` expansion. On Windows, `~` is the current user's profile
directory. There is no default base, no base identity file and no `--replace`:
to move a base, edit its `path` in `config.json`.

Every base-bound command takes `--base NAME` after the command, for example
`kgd agent search QUERY --base research`. Without `--base`, the base is the
registered root that contains the working directory's real path; there is no
upward search for a `.knowledge` directory. Outside every registered root the
command refuses and lists the registered bases. Relative path arguments are
resolved against the working directory.

## Home configuration

```json
{
  "bases": {
    "research": {
      "path": "~/research",
      "sources": {
        "notes/**/*.md": "research-notes",
        "papers/*/main.tex": "research-notes"
      }
    }
  },
  "embedding": null
}
```

- The top level has exactly `bases` and `embedding`; each base has exactly
  `path` and `sources`. `embedding` is `null` or a model id.
- `path` is stored as `~/…` (forward slashes) when the root lies under the
  user's home, otherwise as an absolute path.
- No base root may be equal to, an ancestor of or a descendant of another, and
  the home may not lie inside a base root.
- Each `sources` key is a glob relative to the base root, expanded with
  Python's `glob.glob(pattern, root_dir=root, recursive=True)`: `*` stays within
  one path segment, `**` spans directories, and hidden files and directories
  (`.knowledge/`, `.obsidian/`, `.git/`) never match. Globs are non-empty,
  relative, use `/`, and contain no `..` or hidden segment.
- Each value names a type in `types/`. A file matched by at least one glob is a
  registered source. Several globs may match one file when they name the same
  type; globs naming two different types for one file are an error, so every
  source has exactly one type.

A type file `types/<name>.md` has a slug stem, YAML frontmatter read with
PyYAML's `BaseLoader` (every scalar stays a string) and a non-empty body, the
extraction guidance:

```markdown
---
node_kinds: [definition, theorem, concept]
relation_kinds:
  implies: [premise, conclusion]
epistemic: [proved, stated]
---
Write the guidance an agent follows when extracting knowledge from these
documents.
```

`node_kinds` is required; `relation_kinds` and `epistemic` are optional. Edit
`sources` and the types by hand; `kgd check` validates the result. Sources are
UTF-8 text documents of any format; kgdistiller reads them as numbered lines
and never parses or converts them.

## Check

```sh
kgd check --base research
kgd check --base research --fix-lines
```

`check` validates every entry and edge (file format, unique ids and names,
registered and readable sources, line ranges, kinds, edge endpoints and the
acyclic `prerequisite-for`) and compares each entry's Evidence quote with its
cited lines. It prints `OK: <n> entries, <m> edges` and exits 0, or lists every
error and every `moved`, `stale` or `ambiguous` entry, ends with
`FAILED: <x> errors, <y> stale entries`, and exits 1.

After editing a source so that cited lines shift, `check --fix-lines` rewrites
the line range of each moved entry (its quote found exactly once elsewhere in
the source) under the home lock (`$KGDISTILLER_HOME/lock`) and checks again. A
stale entry (quote gone) or an ambiguous one (quote found several times) needs
a reviewed re-capture. Staleness is reported only; it never hides an entry from
retrieval or the graph feed.

## Git synchronization

Initialize a private Git repository, commit, add a remote, or push only when
the user explicitly authorizes that action. A base tracks:

- every registered source document and required authored asset;
- `.knowledge/entries/` and `.knowledge/edges.jsonl`.

kgdistiller writes no `.gitignore` into a base, so the base must ignore
`.knowledge/build/` itself, together with credentials and query logs.

The home is separate owner data: keep `config.json`, `types/` and its
`.gitignore` in their own private local Git repository, with no remote by
default. `config.json` and `types/` cannot be derived from a base, so on a
second machine restore them by hand (or from that private repository), then
fix each base `path`. `check` proves local consistency, not that a commit or
remote synchronization happened.

After an ordinary clone or pull:

```sh
kgd check --base research
kgd agent status --base research
kgd agent resolve "KNOWN NAME" --base research
```

If `check` fails on a clean checkout, restore a known-good revision or repair
the source on its owning machine. Never delete an interrupted ingest journal
under `.knowledge/build/kgdistiller-ingest/`; the next `ingest apply` recovers
it.

## Local services and the Obsidian graph feed

`kgd mcp [--base B]` exposes only bounded read-only operations over the one
base selected when it starts.

Install the packaged read-only Obsidian plugin into a selected vault with
`kgd obsidian install --base B`; use `--replace` for an
upgrade. The installer manages only `main.js`, `manifest.json`, and `styles.css`,
preserves `data.json`, and can leave the enabled-plugin list untouched with
`--no-enable`. Open the base root as the editor vault; its
registered sources and `.knowledge/entries/*.md` remain the knowledge.

The plugin reads one derived file, `.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`). Regenerate it with
`kgd export obsidian --base B` after an ingest, a
`check --fix-lines` or a source edit; the command writes it atomically from
every entry and every accepted edge. The plugin's typed graph view shows
concepts (by kind and understanding), source documents, semantic edges and
definition edges; its Open buttons open an entry's own file, or a source at its
cited lines, when the feed lies inside the base's `.knowledge/` tree in the
same vault. The feed lies under the hidden `.knowledge/build/`, which no source
glob matches; never rescan it or ingest it back. An explicit `--output` inside the
base root must stay under `.knowledge/` and outside `entries/`; a path elsewhere
in the base root or inside `.obsidian` is refused.

## Agent runtime integration

`kgdistiller codex link` and `kgdistiller claude link` install the Skills,
agent presets and product root declared by the workflow manifests; the matching
`doctor` command verifies them. Installed copies are product-owned: `doctor`
reports a copy that differs from the product source, and relinking replaces it
(and deletes retired copies), discarding any local edits to the installed
files. Change the product checkout, not the installed copy.

## Deployment receipt

Record the base name, its root, the home path, the installed kgdistiller
version and exact product commit when known, the entry and edge counts from
`agent status`, the `check` result, and Git commit/remote state only when
actually confirmed. Never include full source or entry content, credentials, or
unbounded excerpts.
