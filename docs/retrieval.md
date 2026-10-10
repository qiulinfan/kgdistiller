# Index and retrieval

Every read goes through one derived database, `$KGDISTILLER_HOME/index.sqlite`.
`kgd index` is its only writer; `search`, `resolve`, `get` and the MCP server
read it. The database is a pure function of `config.json` and the
`entries/*.md` files of the registered, available bases ([model.md](model.md)):
it may lag behind the files, and every read reports that lag, but it never
differs from what a rebuild would produce. Drafts are never indexed.

This release has the lexical phase only. The embedding phase and the dense
search lane arrive in a later release (S3); until then `vec` is always NULL,
`meta.embedding` is always `''`, search fuses the lexical and name lanes, and
`kgd index --no-embed` behaves exactly like `kgd index`.

## Engine

The database is one SQLite file in WAL mode, built with the standard library
`sqlite3` module and FTS5 over pre-tokenized text. It needs no SQLite
extension loading. Every open checks for FTS5 and fails with a clear message
when the SQLite build lacks it. The trigram tokenizer and contentless FTS
tables are not used.

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
  text          TEXT NOT NULL,                 -- unified text: FTS (and later embedding) input
  vec           BLOB                           -- NULL in this release
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

Each record has one text, used for FTS:

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
label change cascades to every record that links it.

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

1. With `--rebuild`, delete every row of `record`, `link`, `name` and `fts`
   inside the same transaction. The file is never swapped, so WAL readers keep
   their snapshot until commit.
2. Delete the rows of bases no longer in `config.json`. Leave the rows of
   registered bases whose root is unavailable untouched and report those bases.
3. For each available base, list the regular `entries/*.md` files; a
   symlinked record file is reported as unparseable and a symlinked
   `.knowledge` or `entries/` refuses the run. A file is parsed when it has
   no row or its `(st_mtime_ns, st_size)` differs from its row; a row whose
   file is gone is deleted.
4. Parse structurally (frontmatter shape, value grammar, body grammar). Types,
   kinds, link existence and evidence freshness belong to `check`. An
   unparseable file keeps no row and is reported, so it is re-parsed on every
   run until fixed. Values resolve to uids textually: `[[x]]` in base `b`
   becomes `b:x`, `[[c:x]]` becomes `c:x`.
5. Upsert with `INSERT … ON CONFLICT(uid) DO UPDATE`, which keeps the rowid,
   then replace the record's `link` and `name` rows. A changed understanding
   is reported in `understanding_changed`.
6. Recompute the unified text of every row from the stored labels and links.
   Where it differs from the stored text, rewrite the `fts` row (delete and
   insert by rowid).
7. Commit.

**Report.**

```json
{"created": false, "rebuild": false,
 "bases": {"notes": {"parsed": 3, "deleted": 0, "unparseable": [{"path": "/abs/…/x.md", "message": "…"}]}},
 "unavailable": [], "understanding_changed": [{"uid": "notes:measure", "from": "unknown", "to": "understood"}],
 "reused": 0, "embedded": 0, "unembedded": 0, "truncated": []}
```

`created` says the file was (re)created. `reused`, `embedded` and `truncated`
belong to the embedding phase and stay `0` and `[]` in this release;
`unembedded` counts the rows when `config.embedding` is set and is `0`
otherwise. The exit code is 1 when a file was unparseable or a base was
unavailable.

**Invariants** (tested). After any sequence of file edits, an incremental
`kgd index` produces the same rows as `kgd index --rebuild` and as a build from
a deleted database, comparing `record` (without rowid, mtime_ns and size),
`link`, `name`, and `fts` joined by uid. No module of the package imports
`hashlib`.

**Restore.** Delete `index.sqlite*` in the home and run `kgd index`.

## Reads and lag

`search`, `resolve`, `get` and the MCP server open the database read-only
(`file:…?mode=ro`); they never create, migrate or write it. A missing database
is an error that says to run `kgd index`. Every result carries:

```json
"lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false}
```

- `changed_files`: new, modified or deleted `entries/*.md` compared with the
  rows by stat, plus rows of bases no longer registered.
- `unavailable_bases`: registered bases whose root is missing.
- `unembedded`: rows with `vec IS NULL` when `meta.embedding` is set; `0` in
  this release.
- `embedding_changed`: `config.embedding` (or `''`) differs from
  `meta.embedding`. With an embedding model configured it is `true` until the
  dense lane exists.

When `changed_files` is above 0, run `kgd index` and repeat the read.

## Filters

`search` and `resolve` take the same repeatable filters: `--base B`,
`--kind K`, `--class node|relation`, `--source PREFIX` (a base-relative source
path prefix) and `--understanding U`. Values are ORed within a filter and ANDed
across filters, applied in SQL in every lane. `get` addresses records by uid
and takes no filters.

## `kgd search QUERY [--limit 40] [filters]`

Two lanes each produce a ranked uid list:

1. **Lexical.** `fts MATCH` over the OR of the deduplicated, quoted
   `tokens(QUERY)`, ordered by `bm25(fts)`, top 200.
2. **Name.** Candidate keys are every contiguous run of 1–12 words of
   `name_key(QUERY)`, plus every substring of length 1–12 of each CJK run. They
   are looked up in `name`; the ranking is: key equal to `name_key(QUERY)`
   first, then longer keys, then labels before aliases, then uid.

Reciprocal rank fusion combines them: score = Σ 1/(60 + rank), ranks from 1,
ties broken by uid. There are no boosts, intent rules or thresholds.

```json
{"query": "measure space", "lanes": ["lexical", "name"],
 "lag": {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": false},
 "results": [{"uid": "notes:measure-space", "class": "node", "kind": "definition", "label": "Measure space",
   "base": "notes", "source": "notes/math/measure-theory/chapters/01-sigma-algebra.tex",
   "lines": "206-224", "understanding": "understood", "epistemic": null, "gloss": "三元组 …",
   "ranks": {"lexical": 3, "name": 1},
   "requires": [{"uid": "notes:sigma-algebra", "label": "σ-algebra"}, {"term": "measurable space"}],
   "participants": {},
   "in": [{"uid": "notes:probability-space", "kind": "definition", "role": "requires"}],
   "truncated": {"requires": 0, "participants": 0, "in": 0}}]}
```

A rank is `null` for a lane that did not return the record. `participants`
(relations only) maps each role to its values; `in` lists the records that
link this one, with the linking role. `requires`, each role list and `in` are
capped at 12 entries; `truncated` counts what was cut. Pending terms are not
search results; use `resolve`.

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

## MCP server: `kgd mcp`

`kgd mcp` takes no arguments. It is a read-only stdio server over the whole
home with three tools that mirror the CLI and return the same JSON:

| Tool | Arguments |
|---|---|
| `kg_search` | `query` (required), `limit` (1–500, default 40), filters `base`, `kind`, `class`, `source`, `understanding` (arrays) |
| `kg_resolve` | `terms` (required array), the same filters |
| `kg_get` | `uids` (required array), `source_lines` (0–200) |

Inputs are bounded and unknown arguments are rejected. Each call opens a fresh
read-only connection, so it sees every `kgd index` that committed since the
previous call. A missing database is a tool error that says to run
`kgd index`. Responses are capped at 8 MiB. There are no write tools; Skills
write through the CLI.

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
40-paper S5b library (222 questions, deterministic candidate recall):

| Candidate lanes | Full@40 |
|---|---|
| FTS5 over whole records (lexical alone) | 85.6 |
| FTS5 + dense | 90.5 |
| FTS5 + dense + name | 91.0 |

Whole-record BM25 scored 53.6 against 45.5 for passage BM25, which is why the
unit of retrieval is the record. Using graph closure as a ranking lane raised
Full@40 but cut Full@10 from 73.9 to 63.1, so closure is delivered as links on
each result instead. On the 316 notes records, the name lane ranks
`notes:measure-space` first for the query "measure space".
