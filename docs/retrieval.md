# Index and retrieval

Every read goes through one derived database, `$KGDISTILLER_HOME/index.sqlite`.
`kgd index` is its only writer; `search`, `resolve`, `get`, `neighbors`,
`browse`, `pack` and the MCP server read it. The database is a pure function of `config.json`, the
`entries/*.md` files of the registered, available bases ([model.md](model.md))
and the embedding model named by `config.json`: it may lag behind the files,
and every read reports that lag, but it never differs from what a rebuild would
produce. Drafts are never indexed.

The retrieval units are accepted knowledge records, both nodes and relations,
selected under the owner's source-type policy. Original articles are not
independently split into ranked paragraphs. A retrieved record supplies its
base, source path, lines and evidence; `get --source-lines` reads surrounding
source text live when the agent needs more detail. Wider context can be read
from the original file using the registered base root and that relative path.

## Engine

The database is one SQLite file in WAL mode, built with the standard library
`sqlite3` module and FTS5 over pre-tokenized text. Each record's vector is a
little-endian float32 (`<f4`), L2-normalized BLOB in the same table, and the
dense lane is an exact NumPy dot-product scan over those BLOBs. It needs no
SQLite extension loading. Every open checks for FTS5 and fails with a clear
message when the SQLite build lacks it. The trigram tokenizer and contentless
FTS tables are not used.

NumPy, PyTorch, transformers and sentencepiece come only with the
`retrieval` extra; without
it, `embedding` stays `null` and search runs only the lexical lane and the name
lane. The exact scan is revisited when a home exceeds 50k records or a cold
vector load takes more than 1 s; the next step would be a vector index over the
same BLOBs, with no change to the record files.

## Schema (`PRAGMA user_version = 4`)

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- one row: ('embedding', <model id that produced every non-NULL vec, or ''>)

CREATE TABLE record(
  rowid         INTEGER PRIMARY KEY,           -- stable: upserted with ON CONFLICT(uid) DO UPDATE
  uid           TEXT NOT NULL UNIQUE,          -- base:id
  base          TEXT NOT NULL,
  id            TEXT NOT NULL,
  kind          TEXT NOT NULL,
  label         TEXT NOT NULL,
  aliases       TEXT NOT NULL,                 -- JSON array
  source        TEXT NOT NULL,                 -- base-relative path, or a foreign record's web address
  line_start    INTEGER NOT NULL,              -- first line of the first cited range; 0 when foreign
  line_end      INTEGER NOT NULL,              -- last line of the last cited range; 0 when foreign
  lines         TEXT NOT NULL,                 -- every cited range, "a-b, c-d"; '' when foreign
  understanding TEXT NOT NULL,                 -- yes | no
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
  dst      TEXT NOT NULL,                      -- target uid; may name a missing record
  PRIMARY KEY (src, role, pos)
) WITHOUT ROWID;
CREATE INDEX link_dst  ON link(dst);

CREATE TABLE name(
  key TEXT NOT NULL, uid TEXT NOT NULL, is_label INTEGER NOT NULL,   -- name_key(label) and of each alias
  PRIMARY KEY (key, uid)
) WITHOUT ROWID;

CREATE VIRTUAL TABLE fts USING fts5(tokens, tokenize = 'unicode61 remove_diacritics 0');
-- rowid = record.rowid; tokens = ' '.join(tokens(record.text))
```

There is no file table (a row carries its own stat), no base table (bases come
from `config.json`), no tree or passage table (the tree is the `source` path),
no foreign column (a record is foreign when its `source` is a web address),
and no document-type or class column, so editing `types/` never forces
re-indexing. A record's class is read at query time from the types of its
base: it is `relation` when one of them declares its kind under
`relation_kinds`, and `node` otherwise ([model.md](model.md) "Class rule").
`kgd index` and `kgd base list` load `config.json` only, and the reads also
load the types for the class: a malformed type file fails `check`, `source`
and `accept`, never the index or retrieval, which read its relation kinds as
none.

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

Each `{v}` is the linked record's label, or its uid when that record is
missing. The text excludes understanding, the record's own
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
`user_version` ≠ 4, it is deleted together with `-wal` and `-shm`, created
with `meta.embedding = ''` and fully built. This is the one-command restore; a
derived file needs no compatibility path. Before deleting a file that still
reads, `index` takes its `meta.embedding` and its vectors by text: when that
model is the configured one, the new database starts with it and every record
whose unified text is unchanged keeps its vector, so a schema change never
forces a re-embed.

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
 "unavailable": [], "understanding_changed": [{"uid": "notes:measure", "from": "no", "to": "yes"}],
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
the only slow part, at about 14 records a second with `BAAI/bge-m3` on Apple
silicon. `kgd index --rebuild` re-uses the stored vectors by text and loads no
model.

## When records are embedded

A record is embedded when it is admitted: `kgd accept` ends with an index run
whose embedding phase covers only the records it created or replaced. A record
edited by hand after admission is
re-embedded only when the owner asks, with a plain `kgd index`; Skills and the
Obsidian plugin refresh the keyword index after edits with
`kgd index --no-embed` and never re-embed on their own.

Whether a re-embed has work needs no snapshot or hash. Each row keeps the
unified text its vector was computed from, and the lexical phase sets `vec`
to NULL only when that text changes (step 7), so `unembedded` counts exactly
the records whose embedding input changed. Touching a file, or changing a
field the unified text leaves out such as `understanding` or `lines`, leaves
the vector current; renaming a record that others require changes their text
too, since it carries the required labels. The Obsidian plugin's Re-embed
button is solid with that count while it is above 0 or the model changed, and
grey otherwise. See "The embedding process" for what an embedding costs.

## The embedding process

`embedding` names a Hugging Face id of an XLM-RoBERTa sentence encoder that ships `sentencepiece.bpe.model`, such as `BAAI/bge-m3`, and kgdistiller runs it its own
way (`adapters/embedding.py`): transformers' XLM-RoBERTa network with every
Linear and Embedding stored as int8 rows with one scale each, the vocabulary
table kept in CPU memory, sentencepiece for tokens, and the model's own
sentence-transformers pooling (CLS or mean) and prompts. The rest runs on CUDA
or MPS in float16 when present, otherwise on the CPU in float32. Weights are
read tensor by tensor from a memory map and quantized in row chunks. Texts are
encoded one at a time and truncated to the model's limit.

`meta.embedding` records the model with its precision, such as
`BAAI/bge-m3 int8`, so vectors of another precision count as another model.

`kgd embedding` shows the model, the precision, what the stored vectors were
computed with, how many records await a re-embed, and as `cached` the models
in the local Hugging Face cache kgdistiller can run (an XLM-RoBERTa
configuration with `sentencepiece.bpe.model`, a pooling configuration and
weights, read from the cache's files alone); `kgd embedding set
MODEL` downloads the model into the Hugging Face cache, checks that
kgdistiller can run it (its configuration first, so another architecture is
refused before its weights download) and then writes it into `config.json`
under the home lock; `kgd embedding off` writes `null`. Neither re-embeds.
The next index run of any kind, `--no-embed` included, sees that
`meta.embedding` differs and drops every vector of the old model (lexical
phase, step 1), and the next plain `kgd index` embeds every record with the
new model.

Measured on 2026-10-15 with `BAAI/bge-m3` on Apple silicon, against its
float32 vectors on a 2635-record base with 210 questions: int8 kept 96.8% of
each question's dense top 10 and every float32 first hit within the top 10,
and every document vector at cosine 0.998 or more. float16 kept 99.7% but
needs twice the memory; fp8 (e4m3) kept 94.8% at int8's size; int4 kept
75.6%. The process takes about 1 GB (float32 3.2 GB); 0.3 GB of that is
Python, PyTorch and MPS. It encodes about 14 records a second; batching on
MPS reached 35 a second but doubled the memory. sentencepiece tokenized 2844
of 2845 texts exactly as the model's 0.33 GB fast tokenizer did, the other
splitting `}}}` the other way.

At most one model is in memory per user. `kgd index`, `kgd accept`,
`kgd search` and every `kgd mcp` send texts to one embedding
process (`adapters/embed_service.py`) and get vectors back; it never opens the
database, so `kgd index` stays its only writer. The first request starts it,
it loads a model on that model's first request, answers one request (one batch
of 64 texts or one query) at a time, and exits after 15 minutes without a
request. A capture while reading therefore pays the load once per session: on
2026-10-15 a warm `kgd accept` with its embedding took 0.15 s. Its socket (a
named pipe on Windows), key, lock and log live in `kgdistiller-<uid>` under the
user's temporary directory, private to the user. It holds the lock for its
whole life, so a second process exits at once and a socket with no lock holder
is left from one that died; a client that cannot connect starts it again.

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
  dense lane keeps using it until the next index run drops them.

When `changed_files` is above 0, run `kgd index --no-embed` and repeat the
read. `unembedded` and `embedding_changed` weaken only the dense lane, and
re-embedding them is the owner's call (see "When records are embedded").

## Filters

`search`, `resolve`, `neighbors`, `browse` and `pack` take the same repeatable
filters: `--base B`, `--kind K`, `--class node|relation`, `--source PREFIX` (a
base-relative source path prefix) and `--understanding U`. Values are ORed
within a filter and ANDed across filters, applied in SQL. `search` applies
them in every lane. `neighbors` prunes the traversal with them at every hop:
the start uids are always included, and missing targets are always reported
as leaves. `pack` applies them only to the records it adds
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
   model. The query is encoded by the user's one embedding process, so only
   the first dense search after 15 idle minutes waits for the model (about
   2 s for `BAAI/bge-m3` on Apple silicon); a warm `kgd search` took 0.2 s on
   2026-10-15.
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
   "lines": "206-224", "foreign": false, "understanding": "yes", "epistemic": null,
   "gloss": "…", "degree": 14,
   "ranks": {"lexical": 26, "dense": 1, "name": 1},
   "requires": [], "participants": {},
   "in": [{"uid": "notes:complete-measure-space-derived-from-measure-space", "kind": "derived-from", "role": "origin"},
          {"uid": "notes:every-measure-space-can-be-completed-derived-from-measure-space", "kind": "derived-from", "role": "origin"}],
   "truncated": {"requires": 0, "participants": 0, "in": 0}}]}
```

The example shortens `gloss` and `in`. A rank is `null` for a lane that did
not return the record. `participants` maps each role of the record's kind to
its values, a relation's participants or what a statement node is about; `in` lists the records that link this one, with the linking role.
`requires`, each role list and `in` are capped at 12 entries; `truncated`
counts what was cut. `degree` is the number of distinct other records the
record links or is linked by, through roles and `requires`, all bases
included: how connected it is, which is what makes a record central, never its
kind. `foreign` is true when the record's `source` is a web
address, and `lines` is then `""`.

## `kgd resolve TERM... [filters]`

For each term, unranked and unlimited, sorted by base, source and line:

```json
{"terms": [{"term": "measure", "key": "measure",
   "senses":   [{"uid": "notes:measure", "label": "Measure", "kind": "definition", "class": "node",
                 "base": "notes", "source": "…", "lines": "40-52", "foreign": false, "gloss": "…"}],
   "mentions": [{"uid": "notes:measure-space", "label": "Measure space", "…": "…"}]}],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

- `senses`: records whose label or alias key equals `name_key(TERM)`;
- `mentions`: records whose label or alias key contains it as a whole-word
  phrase, or as a substring when the term is CJK.

`resolve` serves identity checks before writing and sense enumeration. A sense
is a candidate, never an identity: compare definitions.

## `kgd get UID... [--source-lines N]`

```json
{"records": [{"uid": "notes:sum-of-two-subspaces-is-a-subspace", "base": "notes", "id": "…",
   "class": "relation", "kind": "implies", "label": "…", "aliases": [], "source": "…", "lines": "24-32",
   "foreign": false, "understanding": "yes", "epistemic": "stated", "gloss": "…", "body": "…",
   "search_terms": "", "evidence": ["…"], "degree": 3,
   "out": [{"role": "premise", "pos": 0, "uid": "notes:subspace", "label": "Subspace", "exists": true},
           {"role": "requires", "pos": 0, "uid": "notes:vector-space", "label": "Vector space", "exists": true}],
   "in": [{"uid": "…", "label": "…", "kind": "…", "role": "…"}],
   "source_text": "23\t…\n24\t…"}],
 "missing": ["notes:unknown-id"],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

A UID is `base:id`, or a bare id when exactly one base has it; a bare id held
by several bases is an error listing them. `out` lists roles first, then
`requires`. `--source-lines N` adds `source_text`: each cited range widened by
N lines on each side, overlapping windows joined, read live from the source
with line numbers, or `null` when the record is foreign, or the source cannot
be read or is not a registered source of its base that resolves inside the
base root (so a
hand-written `../` path, an absolute path or a symlink leaving the root is
never read). This is the only read of source text; no passages are stored.

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
- A link is reported once, as `{from, role, to, depth}`. `from` is always the
  link's `src` and `to` its `dst`, whatever the walk direction. A link is reported when the endpoint
  it is followed from was reached at a depth `d` below the limit and its other
  endpoint was reached too; its `depth` is `d + 1`, the minimum over the
  endpoints it can be followed from.
- Edges are ordered by depth, then `from`, then roles before `requires`, then
  role name and position. `records` maps every reached uid, the starts
  included, to `{label, kind, class, exists, degree}` (JSON output sorts its
  keys); a missing target has `exists: false`, `null` label, kind and class
  and degree 0.
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
                                                  "class": "node", "degree": 5, "exists": true},
             "notes:hahn-decomposition-theorem-prerequisite-for-jordan-decomposition-theorem": {"…": "…"}},
 "missing": [],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}}
```

That is `kgd neighbors notes:hahn-decomposition-theorem --dir in`, shortened.
The usual walks:

- **Dependency closure:** `--role requires --dir out --depth n` lists what a
  record requires, n levels deep.
- **Claim closure:** `--dir in` from a node lists the relations that cite it,
  with the role it plays in each; `--dir both --depth 2` adds their other
  participants, including relation-to-relation hops such as a relation that
  is the witness of another.
- **Applications:** `--dir in --kind example` lists the example records that
  use a node. The kind filter prunes the walk, so only examples are entered.

## `kgd browse [HANDLE] [filters]`

`browse` lists what is indexed, from the bases down to one source. The handle
splits on its first `:` into a base name and a base-relative path or web
address; a path ending in `/` names a directory, any other path or a web
address a source.

| Handle | Returns |
|---|---|
| none | `bases`: every registered base, sorted by name, as `{name, available, records, relations}` |
| `base` | `{base, dir: "", entries}`: the top-level directories and source files of the base, and the web addresses of its foreign records |
| `base:dir/` | `{base, dir, entries}`: the children of that directory |
| `base:path/file` or `base:<web address>` | `{base, source, kinds}`: that source's records grouped by kind |
| any scope with `--kind K` | `records`: every record of those kinds in scope, with all its links |

- Counts are of the records that pass the filters. `relations` counts
  relation records. `available` says whether the base root exists.
- An entry is `{path, type, records, relations}`, with `type` `dir`,
  `source` or, for a web address at the base root, `foreign`. `path` is the full base-relative path, a directory's ending in
  `/`, so `base:` plus the path is the entry's own handle. A directory's
  counts cover its whole subtree. Entries are sorted by path.
- A source's `kinds` are `[{kind, records}]`, sorted alphabetically (the
  document types are never read), each record
  `{uid, label, class, lines, understanding, gloss, links}` in line order.
- A non-empty `--kind` switches any scope (no handle, a base, a directory or a
  source) to a listing of `{uid, label, kind, class, base, source, lines,
  understanding, epistemic, gloss, links}`, ordered by base, source, line and
  uid. It is uncapped; narrow it with the handle or other filters.
- `links` maps each role to its values in link order, `requires` included as
  one role: `{uid, label}`, with `label` `null` for a missing target. It is
  never capped.
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
   in pack order and link order: `missing-target` for a uid with no record, `not-packed` for a record outside the packet (an
   over-budget record therefore also appears as `not-packed` from its
   referrers).

Gaps list `unknown-uid` first, then `over-budget` in the order met, then the
link gaps. Their shapes are `{reason: "unknown-uid", uid}` (the argument as
given), `{reason: "over-budget", uid}`, `{reason: "missing-target", from,
role, uid}` and `{reason:
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
home with eight tools that mirror the CLI and return the same JSON. Its stdin
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
| `kg_drafts` | `base`, `source` (path prefixes) and `names` (arrays); the JSON of `kgd drafts` ([model.md](model.md#kgd-drafts---base-b---source-prefix---name-name)) |
| `kg_find` | `text` (required), `base`, `source` (path prefixes, arrays), `limit` (1–200, default 20); the JSON of `kgd find` ([model.md](model.md#kgd-find-text---base-b---source-prefix---limit-20)) |

Inputs are bounded and unknown arguments are rejected. Each retrieval call
opens a fresh read-only connection, so it sees every `kgd index` that
committed since the previous call; `kg_drafts` reads the draft files instead,
which the database never holds, and takes no lock, and `kg_find` reads the
registered sources. The server loads no
model: `kg_search`'s dense lane asks the user's one embedding process.
`no_dense` skips the dense lane, as `--no-dense` does. A missing database is a
tool error that says to run `kgd index`. Responses are capped at 8 MiB; the
`budget` maximum of 4 MiB keeps a packet and its gaps under that cap. Only
`kg_search`'s dense lane needs the model. There are no write tools; Skills
write through the CLI.

## Evaluation

Primary retrieval metrics are deterministic gold-record recall, Recall@1 and
retrieval latency. Before running the benchmark, freeze the questions, scope,
corpus, type profiles, complete golden record texts and each question's minimal
complete set of required global uids. The gold mapping belongs to the evaluator
and is not exposed to the retriever. Node and relation records are both valid
gold units; same-named records in different bases remain different uids.

For a gold set G and an ordered result list R, deduplicate R in first-occurrence
order. Recall@K is |G intersect R[:K]| / |G|; AllGold@K is 1 exactly when G is
a subset of R[:K]. Report the cutoffs and average per question. Hit@1 reports
whether the first result is a gold uid; it equals Recall@1 only on single-gold
questions. Any acceptable alternative gold sets and their scoring rule must be
declared before the freeze. Runtime scoring uses the frozen identities, not
an LLM's judgment of the answer or additional source context. A nested link
pointer does not count as returning its target record.

Keep ingestion coverage separate from retrieval of existing records. A pure
retrieval benchmark checks before execution that all gold records exist in
the frozen corpus and allowed scope. An end-to-end benchmark keeps unextracted
gold knowledge in its declared denominator and reports those ingestion misses
separately. Knowledge selection and any source-supported alignment of generated
records to ingestion gold are annotation work, not a new per-run retrieval
judge. Valid format, matching names or stored links alone do not establish
scientific identity or faithful extraction.

Time retrieval from the query or process start to the returned ranked record
list; report cold CLI, first dense MCP call and warm MCP separately. An agentic
run returns a budgeted ordered uid list, with planning and tool calls included
in its retrieval time. Score its final selection, and report intermediate
candidate hits only as diagnostics. Generating a prose answer is a separate
stage. A successful search is not a retrieval failure because later answer
generation failed. Keep actual retrieval failures in the declared denominator
and report timeouts, output cutoffs and tool/model budgets.

Use development cases for iteration and keep heldout queries and gold mappings
out of compilation and tuning. Gold annotated after inspecting old outcomes
supports retrospective diagnostics, not a new heldout claim. Empty-gold
controls, ambiguity, source limitations, packing gaps, scientific evidence
quality, answer correctness and model cost are reported separately from
positive-query recall. Model-based content reviews remain diagnostics rather
than deterministic retrieval scores.

The lanes and the unit of retrieval rest on a 40-paper benchmark (222
questions, deterministic candidate recall). Full@40 was 85.6 for FTS5 over
whole records alone, 90.5 with the dense lane and 91.0 with the name lane too.
Whole-record BM25 scored 53.6 against 45.5 for passage BM25, which is why the
unit of retrieval is the record. Using graph closure as a ranking lane raised
Full@40 but cut Full@10 from 73.9 to 63.1, so closure is delivered as links on
each result instead.
