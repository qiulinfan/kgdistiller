# Index and retrieval

Every read goes through one derived database, `$KGDISTILLER_HOME/index.sqlite`.
`kgd index` is its only writer; `search`, `resolve`, `get`, `neighbors`,
`browse`, `pack` and the MCP server read it. The database is a pure function of `config.json`, the
`entries/*.md` files of the registered, available bases ([model.md](model.md))
and the embedding model named by `config.json`: it may lag behind the files,
and every read reports that lag, but it never differs from what a rebuild would
produce. Drafts are never indexed.

## Engine

The database is one SQLite file in WAL mode, built with the standard library
`sqlite3` module and FTS5 over pre-tokenized text. Each record's vector is a
little-endian float32 (`<f4`), L2-normalized BLOB in the same table, and the
dense lane is an exact NumPy dot-product scan over those BLOBs. It needs no
SQLite extension loading. Every open checks for FTS5 and fails with a clear
message when the SQLite build lacks it. The trigram tokenizer and contentless
FTS tables are not used.

NumPy and sentence-transformers come only with the `retrieval` extra; without
it, `embedding` stays `null` and search runs only the lexical lane and the name
lane. The exact scan is revisited when a home exceeds 50k records or a cold
vector load takes more than 1 s; the next step would be a vector index over the
same BLOBs, with no change to the record files.

## Schema (`PRAGMA user_version = 1`)

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- one row: ('embedding', <model id that produced every non-NULL vec, or ''>)

CREATE TABLE record(
  rowid         INTEGER PRIMARY KEY,           -- stable: upserted with ON CONFLICT(uid) DO UPDATE
  uid           TEXT NOT NULL UNIQUE,          -- base:id
  base          TEXT NOT NULL,
  id            TEXT NOT NULL,
  class         TEXT NOT NULL CHECK (class IN ('node','relation')),
  kind          TEXT NOT NULL,
  label         TEXT NOT NULL,
  aliases       TEXT NOT NULL,                 -- JSON array
  source        TEXT NOT NULL,
  line_start    INTEGER NOT NULL,
  line_end      INTEGER NOT NULL,
  understanding TEXT NOT NULL,                 -- unknown | not-yet-understood | understood
  epistemic     TEXT,
  body          TEXT NOT NULL,                 -- prose before "## Search terms" / "## Evidence"
  search_terms  TEXT NOT NULL,                 -- '' when absent
  evidence      TEXT NOT NULL,                 -- JSON array of quotes
  mtime_ns      INTEGER NOT NULL,              -- stat of entries/<id>.md when parsed
  size          INTEGER NOT NULL,
  text          TEXT NOT NULL,                 -- unified text: FTS and embedding input
  vec           BLOB                           -- '<f4' L2-normalized; NULL only when not embedded under meta.embedding
);
CREATE INDEX record_source ON record(base, source, line_start);
CREATE INDEX record_kind   ON record(kind);

CREATE TABLE link(
  src      TEXT NOT NULL,                      -- record uid
  role     TEXT NOT NULL,                      -- 'requires' or a role name
  pos      INTEGER NOT NULL,                   -- position in that list; repeats allowed
  dst      TEXT,                               -- target uid; may name a missing record
  term     TEXT,                               -- pending term text
  term_key TEXT,                               -- name_key(term)
  CHECK ((dst IS NULL) <> (term IS NULL)),
  PRIMARY KEY (src, role, pos)
) WITHOUT ROWID;
CREATE INDEX link_dst  ON link(dst);
CREATE INDEX link_term ON link(term_key);

CREATE TABLE name(
  key TEXT NOT NULL, uid TEXT NOT NULL, is_label INTEGER NOT NULL,   -- name_key(label) and of each alias
  PRIMARY KEY (key, uid)
) WITHOUT ROWID;

CREATE VIRTUAL TABLE fts USING fts5(tokens, tokenize = 'unicode61 remove_diacritics 0');
-- rowid = record.rowid; tokens = ' '.join(tokens(record.text))
```

There is no file table (a row carries its own stat), no base table (bases come
from `config.json`), no tree or passage table (the tree is the `source` path),
no pending table (pending gaps are `link` rows with `term`) and no
document-type column, so editing `types/` never forces re-indexing. `kgd
index`, the reads and `kgd base list` load `config.json` only: a malformed
type file fails `check`, `sheet`, `accept` and `harvest`, never the index or
retrieval.

## Unified text

Each record has one text, used both for FTS and as the embedding input:

```text
{label}
{aliases joined "; "}                      omitted when empty
{kind}[ · {epistemic}]
{body}                                     omitted when empty
{search_terms}                             omitted when empty
{role}: {v1}; {v2}; …                      one line per role, roles in name order
requires: {v1}; {v2}; …                    last
> {quote}                                  one line per quote, whitespace-normalized
{source}
```

Each `{v}` is the linked record's label, its uid when that record is missing,
or the pending term verbatim. The text excludes understanding, the record's own
id, line numbers, the base name and paths under `.knowledge`. Linked labels are
part of the text, so a relation is found by its participants' names, and a
label change cascades to every record that links it. A text longer than the
model's `max_seq_length` (in tokens, with the model's document prompt) is
still embedded from its leading part, and its uid is listed under `truncated`
in the index report; it is never cut silently.

## Tokens and name keys

Both live in `kgdistiller/index.py`.

- `tokens(s)`: NFKC, casefold, Unicode words (runs of letters and digits),
  plus every character and every adjacent character pair of each CJK run, with
  no lexicon. `测度` therefore matches a record containing `测度论`.
- `name_key(s) = " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", s).casefold()))`.

## `kgd index [--rebuild] [--no-embed]`

`index` always covers every registered base.

**Opening.** If `index.sqlite` is missing, cannot be opened or has
`user_version` ≠ 1, it is deleted together with `-wal` and `-shm`, created
with `meta.embedding = ''` and fully built. This is the one-command restore; a
derived file needs no compatibility path.

**Lexical phase**, in one `BEGIN IMMEDIATE` transaction with a 30 s busy
timeout:

1. If `config.embedding` (or `''` when null) differs from `meta.embedding`,
   set every `vec` to NULL and store the new model id in `meta.embedding`.
2. Keep a pool of vectors keyed by text. With `--rebuild`, delete every row
   of `record`, `link`, `name` and `fts` inside the same transaction, putting
   each deleted `(text, vec)` into the pool, except the rows of registered
   bases whose root is unavailable (step 3). The file is never swapped, so WAL
   readers keep their snapshot until commit.
3. Delete the rows of bases no longer in `config.json`. Leave the rows of
   registered bases whose root is unavailable untouched and report those bases.
4. For each available base, list the regular `entries/*.md` files; a
   symlinked record file is reported as unparseable and a symlinked
   `.knowledge` or `entries/` refuses the run. A file is parsed when it has
   no row or its `(st_mtime_ns, st_size)` differs from its row; a row whose
   file is gone is deleted.
5. Parse structurally (frontmatter shape, value grammar, body grammar). Types,
   kinds, link existence and evidence freshness belong to `check`. An
   unparseable file keeps no row and is reported, so it is re-parsed on every
   run until fixed. Values resolve to uids textually: `[[x]]` in base `b`
   becomes `b:x`, `[[c:x]]` becomes `c:x`.
6. Upsert with `INSERT … ON CONFLICT(uid) DO UPDATE`, which keeps the rowid,
   then replace the record's `link` and `name` rows. A changed understanding
   is reported in `understanding_changed`. Every deleted row (steps 3–5) puts
   its `(text, vec)` into the pool.
7. Recompute the unified text of every row from the stored labels and links.
   Where it differs from the stored text, put the old `(text, vec)` into the
   pool, rewrite the `fts` row (delete and insert by rowid) and set
   `vec = pool[new text]`, or NULL when the pool has no vector for that text.
   The text contains no id, so a renamed record keeps its vector; these rows
   are counted as `reused`.
8. Commit.

**Embedding phase**, after that commit. It is skipped with `--no-embed` or
when `embedding` is null. The model loads only when some row has
`vec IS NULL`, so an up-to-date index never loads it. Those rows are encoded
in batches of 64, ordered by rowid, and each batch commits in its own
transaction with one guarded write per row:

```sql
UPDATE record SET vec = ? WHERE uid = ? AND text = ?
  AND (SELECT value FROM meta WHERE key = 'embedding') = ?   -- the model this run used
```

A concurrent text or model change makes the write a no-op, and the row is
picked up by the next run. The guard keeps every stored vector matched to its
row's current text and model without any hashing, even when two runs overlap.
If `embedding` is set but the `retrieval` extra is missing, the lexical commit
stands and the report's `embedding_error` is `embedding is set to <model> but
the retrieval extra is missing: install kgdistiller[retrieval] or set
embedding to null`. A model that fails to load or encode also sets
`embedding_error` after the lexical commit; `embedded` says how many rows were
written before the failure; rerun `kgd index`.

**Report.**

```json
{"created": false, "rebuild": false,
 "bases": {"notes": {"parsed": 3, "deleted": 0, "unparseable": [{"path": "/abs/…/x.md", "message": "…"}]}},
 "unavailable": [], "understanding_changed": [{"uid": "notes:measure", "from": "unknown", "to": "understood"}],
 "reused": 1, "embedded": 2, "unembedded": 0, "truncated": [], "embedding_error": null}
```

`created` says the file was (re)created. `reused` counts changed rows that
took a vector from the pool, `embedded` the vectors written by the embedding
phase, `unembedded` the rows still without a vector when `embedding` is set
(`0` when it is null), and `truncated` the uids whose text exceeded the
model's input limit, and `embedding_error` the embedding failure or `null`.
The exit code is 1 when a file was unparseable, a base was unavailable or
embedding failed; an embedding failure leaves the lexical phase committed.

**Invariants** (tested with a fake encoder).

1. After any sequence of file edits, an incremental `kgd index` produces the
   same rows as `kgd index --rebuild` and as a build from a deleted database,
   comparing `record` (without rowid, mtime_ns and size, with `vec`), `link`,
   `name`, and `fts` joined by uid.
2. Every non-NULL `vec` is the encoding of its own row's current `text` under
   `meta.embedding`.
3. No module of the package imports `hashlib`.

**Restore.** Delete `index.sqlite*` in the home and run `kgd index`. The
lexical tables come back identical and every record is re-embedded, which is
the only slow part. Measured on a notes base of 535 records (316 nodes and 219
relations) with `"embedding": "BAAI/bge-m3"` on Apple silicon (MPS), offline:
the restore took 115 s and reproduced every `record`, `link`, `name`, `fts` and
`meta` row and all 535 vectors byte for byte; `kgd index --rebuild` on the
embedded database took 0.26 s and re-used all 535 vectors without loading the
model; a no-op `kgd index` took 0.07 s.

## Reads and lag

`search`, `resolve`, `get`, `neighbors`, `browse`, `pack` and the MCP server
open the database read-only (`file:…?mode=ro`); they never create, migrate or write it. Each call reads
inside one read transaction, so everything it returns, `meta.embedding` and the
vectors included, comes from one commit even while `kgd index` writes. A
missing database is an error that says to run `kgd index`. Every result
carries:

```json
"lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}
```

- `changed_files`: new, modified or deleted `entries/*.md` compared with the
  rows by stat, plus rows of bases no longer registered.
- `unavailable_bases`: registered bases whose root is missing.
- `unembedded`: rows with `vec IS NULL` when `meta.embedding` is set, `0`
  otherwise; the dense lane does not see them.
- `embedding_changed`: `config.embedding` (or `''`) differs from
  `meta.embedding`; the stored vectors belong to the previous model, and the
  dense lane keeps using it until `kgd index` re-embeds.

When `changed_files` is above 0, `unembedded` is above 0 or
`embedding_changed` is true, run `kgd index` and repeat the read.

## Filters

`search`, `resolve`, `neighbors`, `browse` and `pack` take the same repeatable
filters: `--base B`, `--kind K`, `--class node|relation`, `--source PREFIX` (a
base-relative source path prefix) and `--understanding U`. Values are ORed
within a filter and ANDed across filters, applied in SQL. `search` applies
them in every lane. `neighbors` prunes the traversal with them at every hop:
the start uids are always included, and pending terms and missing targets are
always reported as leaves. `pack` applies them only to the records it adds
itself (the `requires` closure and shared relations), never to the uids the
caller names. `get` addresses records by uid and takes no filters.

## `kgd search QUERY [--limit 40] [--no-dense] [filters]`

A `QUERY` that is empty after trimming is refused before the database opens
or a model loads. Up to three lanes each produce a ranked uid list:

1. **Lexical.** `fts MATCH` over the OR of the deduplicated, quoted
   `tokens(QUERY)`, ordered by `bm25(fts)`, top 200.
2. **Dense.** Runs only when `meta.embedding` is set and `--no-dense` is
   absent. The query is encoded with `meta.embedding`, the model that produced
   the stored vectors, not with `config.embedding`. The lane takes an exact
   dot product of the query vector with every non-NULL `vec` that passes the
   filters, top 200, ties broken by uid. When stored vectors match the
   filters and the `retrieval` extra is missing, search fails with `install
   kgdistiller[retrieval] or pass --no-dense (MCP: no_dense)`; a model that
   cannot be loaded fails with the same `--no-dense` hint. When no stored
   vector matches, the dense lane is empty and needs neither NumPy nor the
   model. Every CLI process loads the model again: a cold `kgd search` on the
   535-record base took 5.5–6.4 s with `BAAI/bge-m3`, against 0.06 s with
   `--no-dense`; the MCP server keeps the model resident.
3. **Name.** Candidate keys are every contiguous run of 1–12 words of
   `name_key(QUERY)`, plus every substring of length 1–12 of each CJK run. They
   are looked up in `name`; the ranking is: key equal to `name_key(QUERY)`
   first, then longer keys, then labels before aliases, then uid.

Reciprocal rank fusion combines them: score = Σ 1/(60 + rank), ranks from 1,
ties broken by uid. There are no boosts, intent rules or thresholds. `lanes`
lists the lanes that ran, in this order, and every result's `ranks` has
exactly those keys; with `--no-dense`, or when `meta.embedding` is `''`
(embedding null), they are `lexical` and `name`.

```json
{"query": "measure space", "lanes": ["lexical", "dense", "name"],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false},
 "results": [{"uid": "notes:measure-space", "class": "node", "kind": "definition", "label": "measure space",
   "base": "notes", "source": "notes/math/measure-theory/chapters/01-sigma-algebra-与-measure.tex",
   "lines": "206-224", "understanding": "unknown", "epistemic": null,
   "gloss": "…",
   "ranks": {"lexical": 26, "dense": 1, "name": 1},
   "requires": [], "participants": {},
   "in": [{"uid": "notes:complete-measure-space-derived-from-measure-space", "kind": "derived-from", "role": "origin"},
          {"uid": "notes:every-measure-space-can-be-completed-derived-from-measure-space", "kind": "derived-from", "role": "origin"}],
   "truncated": {"requires": 0, "participants": 0, "in": 0}}]}
```

The example shortens `gloss` and `in`. A rank is `null` for a lane that did
not return the record. `participants` (relations only) maps each role to its
values; `in` lists the records that link this one, with the linking role.
`requires`, each role list and `in` are capped at 12 entries; `truncated`
counts what was cut. Pending terms are not search results; use `resolve`.

## `kgd resolve TERM... [filters]`

For each term, unranked and unlimited, sorted by base, source and line:

```json
{"terms": [{"term": "measure", "key": "measure",
   "senses":   [{"uid": "notes:measure", "label": "Measure", "kind": "definition", "class": "node",
                 "base": "notes", "source": "…", "lines": "40-52", "gloss": "…"}],
   "mentions": [{"uid": "notes:measure-space", "label": "Measure space", "…": "…"}],
   "pending":  [{"owner": "notes:outer-measure", "role": "requires", "term": "measure"}]}],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

- `senses`: records whose label or alias key equals `name_key(TERM)`;
- `mentions`: records whose label or alias key contains it as a whole-word
  phrase, or as a substring when the term is CJK;
- `pending`: `link` rows whose `term_key` equals it.

`resolve` serves identity checks before writing and sense enumeration. A sense
is a candidate, never an identity: compare definitions.

## `kgd get UID... [--source-lines N]`

```json
{"records": [{"uid": "notes:sum-of-two-subspaces-is-a-subspace", "base": "notes", "id": "…",
   "class": "relation", "kind": "implies", "label": "…", "aliases": [], "source": "…", "lines": "24-32",
   "understanding": "unknown", "epistemic": "stated", "gloss": "…", "body": "…", "search_terms": "",
   "evidence": ["…"],
   "out": [{"role": "premise", "pos": 0, "uid": "notes:subspace", "label": "Subspace", "exists": true},
           {"role": "requires", "pos": 0, "term": "vector space"}],
   "in": [{"uid": "…", "label": "…", "kind": "…", "role": "…"}],
   "source_text": "23\t…\n24\t…"}],
 "missing": ["notes:unknown-id"],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

A UID is `base:id`, or a bare id when exactly one base has it; a bare id held
by several bases is an error listing them. `out` lists roles first, then
`requires`. `--source-lines N` adds `source_text`: the cited range widened by
N lines on each side, read live from the source with line numbers, or `null`
when the source cannot be read or is not a registered source of its base that
resolves inside the base root (so a hand-written `../` path, an absolute path
or a symlink leaving the root is never read). This is the only read of source
text; no passages are stored.

## `kgd neighbors UID... [--role R]... [--dir out|in|both] [--depth 1] [filters]`

`neighbors` walks the links from the given records with one recursive CTE over
`link`. It infers nothing: every edge it returns is a stored link.

- The walk starts from the given uids at depth 0. `--dir out` follows a link
  from its `src` to its `dst`, `--dir in` from its `dst` to its `src`, and
  `--dir both` either way. `--role R` (repeatable) restricts the roles that
  may be followed, at every hop; `requires` is a role name like any other.
- `--depth N` (default 1) bounds the walk. The CTE deduplicates `(uid, depth)`
  pairs, so a cycle ends at the depth limit, and each reached uid keeps its
  minimum depth. A target uid with no record is reached but never followed
  further.
- A link is reported once, as `{from, role, to, depth}` or, for a pending
  term, `{from, role, term, depth}`. `from` is always the link's `src` and `to`
  its `dst`, whatever the walk direction. A link is reported when the endpoint
  it is followed from was reached at a depth `d` below the limit and its other
  endpoint was reached too; its `depth` is `d + 1`, the minimum over the
  endpoints it can be followed from. Pending terms are leaves of the `out`
  direction, so they appear with `--dir out` and `--dir both` only.
- Edges are ordered by depth, then `from`, then roles before `requires`, then
  role name and position. `records` maps every reached uid, the starts
  included, to `{label, kind, class, exists}` (JSON output sorts its keys); a
  missing target has `exists: false` and `null` label, kind and class.
- A start that names no record goes to `missing`, as in `get`; a bare id held
  by several bases is an error. `--dir` must be `out`, `in` or `both`, and
  `--depth` at least 1.

```json
{"edges": [{"from": "notes:hahn-decomposition-theorem-prerequisite-for-jordan-decomposition-theorem",
            "role": "prerequisite", "to": "notes:hahn-decomposition-theorem", "depth": 1},
           {"from": "notes:hahn-decomposition-theorem-prerequisite-for-radon-nikodym-theorem",
            "role": "prerequisite", "to": "notes:hahn-decomposition-theorem", "depth": 1},
           {"from": "notes:signed-measure-prerequisite-for-hahn-decomposition-theorem",
            "role": "dependent", "to": "notes:hahn-decomposition-theorem", "depth": 1}],
 "records": {"notes:hahn-decomposition-theorem": {"label": "Hahn Decomposition Theorem", "kind": "theorem",
                                                  "class": "node", "exists": true},
             "notes:hahn-decomposition-theorem-prerequisite-for-jordan-decomposition-theorem": {"…": "…"}},
 "missing": [],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

That is `kgd neighbors notes:hahn-decomposition-theorem --dir in`, shortened.
The usual walks:

- **Dependency closure:** `--role requires --dir out --depth n` lists what a
  record requires, n levels deep, with pending terms as leaves.
- **Claim closure:** `--dir in` from a node lists the relations that cite it,
  with the role it plays in each; `--dir both --depth 2` adds their other
  participants, including relation-to-relation hops such as a relation that
  is the witness of another.
- **Applications:** `--dir in --kind example` lists the example records that
  use a node. The kind filter prunes the walk, so only examples are entered.

## `kgd browse [HANDLE] [filters]`

`browse` lists what is indexed, from the bases down to one source. The handle
splits on its first `:` into a base name and a base-relative path; a path
ending in `/` names a directory, any other path a source file.

| Handle | Returns |
|---|---|
| none | `bases`: every registered base, sorted by name, as `{name, available, records, relations, pending}` |
| `base` | `{base, dir: "", entries}`: the top-level directories and source files of the base |
| `base:dir/` | `{base, dir, entries}`: the children of that directory |
| `base:path/file` | `{base, source, kinds, pending}`: that source's records grouped by kind, and its pending terms |
| any scope with `--kind K` | `records`: every record of those kinds in scope, with all its links |

- Counts are of the records that pass the filters. `relations` counts
  relation records, and `pending` counts distinct pending terms by name key.
  `available` says whether the base root exists.
- An entry is `{path, type, records, relations, pending}`, with `type` `dir`
  or `source`. `path` is the full base-relative path, a directory's ending in
  `/`, so `base:` plus the path is the entry's own handle. A directory's
  counts cover its whole subtree. Entries are sorted by path.
- A source's `kinds` are `[{kind, records}]`, sorted alphabetically (the
  document types are never read), each record
  `{uid, label, class, lines, understanding, gloss, links}` in line order.
  `pending` is `[{term, owners: [{uid, role}]}]`, grouped by name key, sorted
  by key, with the first spelling as `term` and each owner listed once per
  role. This is the database twin of the source's sheet.
- A non-empty `--kind` switches any scope (no handle, a base, a directory or a
  source) to a listing of `{uid, label, kind, class, base, source, lines,
  understanding, epistemic, gloss, links}`, ordered by base, source, line and
  uid. It is uncapped; narrow it with the handle or other filters.
- `links` maps each role to its values in link order, `requires` included as
  one role: `{uid, label}` for a link (`label` is `null` for a missing
  target) and `{term}` for a pending term. It is never capped.
- Only sources with indexed records appear. A path with no matching records
  returns empty lists; an unregistered base is the error `unknown base 'x'`.
  Every result carries `lag`.

## `kgd pack UID... [--budget 60000] [--requires-depth 1] [filters]`

`pack` returns whole records for a set of uids within a byte budget, together
with the relations they share and every link it could not follow.

1. **Order.** The given uids, deduplicated in caller order; an unknown one
   becomes an `unknown-uid` gap and a bare id held by several bases is an
   error. Then their `requires` closure, breadth first, up to
   `--requires-depth` layers (default 1; 0 adds none): within a layer, parents
   in order and their `requires` links in position order, keeping targets
   that have a record, pass the filters and are not yet listed.
2. **Records.** Each uid in that order is serialized like `get`, without `in`,
   `search_terms` and `source_text`, and added while the size stays within
   `--budget` (default 60000). The size, reported as `bytes`, is the UTF-8
   byte length of the compact JSON of the `records` list (sorted keys, no
   spaces, non-ASCII kept; `[]` is 2 bytes). A record that does not fit
   becomes an `over-budget` gap and is never truncated; later, smaller
   records may still fit.
3. **Shared relations.** One pass over the links into the packed records
   finds every relation not yet listed that passes the filters and has at
   least two distinct packed targets under roles other than `requires`. They
   are appended in order of packed participants, most first, then uid, under
   the same budget rule.
4. **Gaps.** Every out-link of a packed record that the packet cannot follow,
   in pack order and link order: `pending` for a term, `missing-target` for a
   uid with no record, `not-packed` for a record outside the packet (an
   over-budget record therefore also appears as `not-packed` from its
   referrers).

Gaps list `unknown-uid` first, then `over-budget` in the order met, then the
link gaps. Their shapes are `{reason: "unknown-uid", uid}` (the argument as
given), `{reason: "over-budget", uid}`, `{reason: "pending", from, role,
term}`, `{reason: "missing-target", from, role, uid}` and `{reason:
"not-packed", from, role, uid}`. Filters never remove a uid the caller named;
a closure record they exclude shows up as `not-packed`. `--budget` must be at
least 1 and `--requires-depth` at least 0.

```json
{"records": [{"uid": "notes:hahn-decomposition-theorem-prerequisite-for-radon-nikodym-theorem",
              "class": "relation", "kind": "prerequisite-for", "…": "…",
              "out": [{"role": "dependent", "pos": 0, "uid": "notes:radon-nikodym-theorem",
                       "label": "Radon-Nikodym Theorem", "exists": true},
                      {"role": "prerequisite", "pos": 0, "uid": "notes:hahn-decomposition-theorem",
                       "label": "Hahn Decomposition Theorem", "exists": true}]}],
 "bytes": 1372, "budget": 60000,
 "gaps": [{"reason": "unknown-uid", "uid": "notes:nothing"},
          {"reason": "not-packed", "from": "notes:hahn-decomposition-theorem-prerequisite-for-radon-nikodym-theorem",
           "role": "dependent", "uid": "notes:radon-nikodym-theorem"},
          {"reason": "not-packed", "from": "notes:hahn-decomposition-theorem-prerequisite-for-radon-nikodym-theorem",
           "role": "prerequisite", "uid": "notes:hahn-decomposition-theorem"}],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

Packing `notes:radon-nikodym-theorem` and `notes:hahn-decomposition-theorem`
instead packs both nodes and appends that relation as shared, with no gaps.

## MCP server: `kgd mcp`

`kgd mcp` takes no arguments. It is a read-only stdio server over the whole
home with six tools that mirror the CLI and return the same JSON. Its stdin
and stdout, like every `kgd` command's output, are UTF-8 whatever the locale
or Windows code page. A `query` that is empty after trimming is refused, as in
`kgd search`.

| Tool | Arguments |
|---|---|
| `kg_search` | `query` (required), `limit` (1–500, default 40), `no_dense` (boolean, default false), filters `base`, `kind`, `class`, `source`, `understanding` (arrays) |
| `kg_resolve` | `terms` (required array), the same filters |
| `kg_get` | `uids` (required array), `source_lines` (0–200) |
| `kg_neighbors` | `uids` (required, 1–128), `role` (array), `dir` (`out`, `in` or `both`, default `out`), `depth` (1–32, default 1), the filters |
| `kg_browse` | `handle` (optional), the filters |
| `kg_pack` | `uids` (required, 1–128), `budget` (1–4194304, default 60000), `requires_depth` (0–32, default 1), the filters |

Inputs are bounded and unknown arguments are rejected. Each call opens a fresh
read-only connection, so it sees every `kgd index` that committed since the
previous call. The embedding model loads lazily on the first dense search and
stays resident; it is reloaded only when `meta.embedding` changes. On the
535-record base the first `kg_search` took 6.2 s and the next one 0.03 s.
`no_dense` skips the dense lane, as `--no-dense` does. A missing database is a
tool error that says to run `kgd index`. Responses are capped at 8 MiB; the
`budget` maximum of 4 MiB keeps a packet and its gaps under that cap. Only
`kg_search`'s dense lane loads the model; `kg_resolve`, `kg_get`,
`kg_neighbors`, `kg_browse` and `kg_pack` load none and leave a resident model
in place. There are no write tools; Skills write through the CLI.

## Evaluation

The primary metric is complete-evidence task success: the fraction of declared
tasks whose actual returned evidence jointly supports every necessary fact,
condition, intended sense and source scope, without unsupported scientific
assertions. Its ceiling is 100%, independent of a ranking cutoff. Check source
availability and evidence-budget feasibility before freezing a benchmark. Keep
failed tasks in the declared denominator; report unsupported requests,
ambiguity and source limitations explicitly rather than dropping or
relabelling cases after evaluation.

Derive and freeze necessary facts and conditions from the question and
original sources before inspecting the selected packet or system answer.
Alternative witnesses satisfy the same requirement only when meaning,
conditions, source scope, units and conventions agree. Record references,
declared links and storage metadata do not establish scientific coverage or
definition equivalence. Use development cases for iteration and keep heldout
questions out of compilation and tuning. Report ranking, candidate coverage,
packing gaps, answer correctness, latency and model cost separately.

Measurements behind the current design, from the design research on the
40-paper benchmark library (222 questions, deterministic candidate recall):

| Candidate lanes | Full@40 |
|---|---|
| FTS5 over whole records (lexical alone) | 85.6 |
| FTS5 + dense | 90.5 |
| FTS5 + dense + name | 91.0 |

Whole-record BM25 scored 53.6 against 45.5 for passage BM25, which is why the
unit of retrieval is the record. Using graph closure as a ranking lane raised
Full@40 but cut Full@10 from 73.9 to 63.1, so closure is delivered as links on
each result instead.

Measured on the 535-record notes base with `BAAI/bge-m3` and `--base notes`,
top three fused results:

| Query | Top results (lexical / dense / name rank) |
|---|---|
| `measure space` | `notes:measure-space` (26 / 1 / 1), `notes:measure` (31 / 7 / 2), `notes:measure-space-derived-from-measurable-space` (3 / 3 / –) |
| `测度` | `notes:probability-measure-derived-from-measure` (5 / 3 / –), `notes:probability-measure` (12 / 1 / –), `notes:borel-cantelli-lemma` (15 / 14 / –) |
| `a set with a sigma-algebra and a countably additive function` | `notes:sigma-algebra` (23 / 3 / 1), `notes:measurable-space-derived-from-σ-algebra` (2 / 7 / –), `notes:measure-contrasts-with-outer-measure` (1 / 14 / –) |

With `--no-dense`, "measure space" still ranks `notes:measure-space` first
through the name lane, but ranks 3 and 4 become other spaces
(`notes:l-p-space-is-a-vector-space`, `notes:banach-space`). No label or alias
is `测度`, so its name lane is empty; 14 of the 535 records contain the word,
and neither `notes:measure` nor `notes:measure-space` is among its top 40
results (the dense lane ranks `notes:measure-space` 48th). The paraphrase
matches no name except `sigma-algebra`; the lexical and dense lanes supply the
related measure-theory records.

### Reproducing the compiled-library benchmark

The 40-paper benchmark behind the measurements above (222 questions) ran
against a compiled library and its own agent tools, which the product no
longer ships. Each of their behaviours maps onto a read primitive:

| Benchmark behaviour | Primitive |
|---|---|
| a 40-candidate search | `search --limit 40` |
| the tree root and a paper's tree | `browse` and `browse base:path` |
| the term inventory and its senses | `resolve` |
| a paper's claims list | `browse --kind K` for a relation kind |
| reading and packing entries | `get` and `pack` (shared relations and gaps included) |
| needs, links and agent-side closure | a result's `requires`, `participants` and `in`, then `neighbors` and `pack --requires-depth` |
| the paper cue | `--source PREFIX` |

Reproducing its numbers takes three steps:

1. Recompile the 40-paper library into records in a scratch base, registered
   in a scratch `KGDISTILLER_HOME` inside the experiment repository, and
   record a gold map from every old reference to its uid, in which claims
   become relation uids.
2. Point OMP at `kgd mcp` with that home (see
   [deployment.md](deployment.md#mcp-server)) and confirm that the run's tool
   list exposes the `mcp__kgdistiller_kg_*` tools before any benchmark run.
3. Run OMP with DeepSeek Flash as before.

The product ships no converter from the old library. Candidate selection,
answer submission and any run guard belong to the experiment, not to the
read-only server.
