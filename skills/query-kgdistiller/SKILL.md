---
name: query-kgdistiller
description: Query a kgdistiller external-brain knowledge base read-only. Use when an Agent must batch-resolve canonical names and aliases, run lexical, optional embedding or graph retrieval, read one entry with its edges, expand a bounded neighborhood, build a source-backed context bundle, or classify a candidate's identity before authoring, including when the user asks to search or recall concepts from their kgdistiller (kgd/kgdt) knowledge base.
---

# Query kgdistiller

Treat kgdistiller as an opaque, read-only external brain. Return a bounded,
evidence-backed result; never load the complete knowledge base into model
context.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Keep the boundary read-only

- Use the bounded query interface instead of reading `.knowledge/entries/` or
  `.knowledge/edges.jsonl` wholesale.
- Never edit a source, entry, edge file or registry.
- Never run `capture`, `ingest`, `harvest`, `check --fix-lines` or another
  writer.
- Never promote lexical, embedding, acronym, translation, or topology similarity
  into identity.

Use the read-only MCP tools when available (`kg_status`, `kg_resolve_concepts`,
`kg_search`, `kg_get_node`, `kg_expand`, `kg_ppr`, `kg_build_context`).
Otherwise use `kgdistiller --repo-root PROJECT agent ...` and consume its JSON
output. Every call loads the entries and edges into one in-memory view; do not
reimplement loading or indexing in the Skill.

Start with `kg_status` or:

```sh
kgdistiller --repo-root PROJECT agent status
```

It returns `kgdistiller-query-status-v1` with the entry and edge counts and the
count of each relation. A read never authorizes a write.

Every entry is returned, including entries whose Evidence quote no longer
matches their source; such staleness is reported only by `kgdistiller check` and
never hides knowledge from retrieval. If an answer depends on a cited source
passage, read the entry's `source`, `line_start`, `line_end` and `evidence`
fields and say when `check` reports that entry as stale.

## Resolve identities first

Resolve the whole batch of names with `kg_resolve_concepts` or:

```sh
kgdistiller --repo-root PROJECT agent resolve "Concept A" "Concept B"
```

Each result has a `status`: `exact` (an id or canonical label), `alias` (a
unique alias), `ambiguous` (several entries share the name) or `missing`.
Names are compared after NFKC normalization, casefolding and whitespace
collapsing. `exact` and `alias` are identity-authoritative; anything else needs
review.

Then read only what is needed:

```sh
kgdistiller --repo-root PROJECT agent get ENTRY_ID
kgdistiller --repo-root PROJECT agent expand ENTRY_ID --direction both --depth 1
kgdistiller --repo-root PROJECT agent ppr ENTRY_ID --limit 10
```

`agent get` returns the entry record (label, kind, aliases, source and line
range, understanding, sections and Evidence, plus its `entry` path) with its
incoming and outgoing edges. `expand` and `ppr` walk the accepted edges.

## Search and build context

For anything beyond exact resolution:

```sh
kgdistiller --repo-root PROJECT agent search "QUESTION OR TERMS"
kgdistiller --repo-root PROJECT agent context "QUESTION" --budget 6000
```

Lexical search is BM25 over each entry's label, aliases, kind, summary,
context, role, list sections and Evidence. Tokens are NFKC/casefolded Unicode
words; CJK text is indexed as single characters and adjacent pairs, so a
Chinese sub-word such as `测度` matches `测度论`. Put source-language forms in
the query when the user reads in another language.

For a precise, repeatable retrieval, write one `kgdistiller-retrieval-plan-v1`
with `question`, `identity_queries` (canonical names and identity-authoritative
aliases), `lexical_queries` (concise discriminating terms), `graph` (`seed_ids`
only after identity is established, `edge_types`, `direction`, `max_depth`,
`strategy`) and `limit`, then run `agent search --plan PLAN.json`. Exact
identity evidence takes precedence over score fusion.

Two optional lanes exist:

- `--embedding` (and `--rerank`) adds the local BGE-M3 embedding lane and a
  cross-encoder reranker. It requires the `kgdistiller[retrieval]` extra and
  returns `kgdistiller-search-execution-v2`. Use it when lexical search misses a
  paraphrased question; use `--models-offline` when weights are already cached.
- `--graph-retrieval` adds bounded graph navigation from the top candidates and
  returns `kgdistiller-search-execution-v3`; `--graph-edge-policy` is
  `high-confidence` (edges declared `confidence: high` with evidence) or `all`.

Embedding, rerank and graph signals only rank review candidates; none of them
creates identity. Preserve each lane's status and reason in the handoff.

### Prerequisite lookup during reading

For a prerequisite used at a specific source step, accept a batch of precise
names plus required statements, domains and conditions. Resolve the names, then
inspect bounded content for applicability. This is a use of existing knowledge,
not an identity merge.

Return the use site, required formulation, verified existing entry and relevant
content, and whether the entry actually supplies the needed step. For example,
distinguish an Lp from an ell-p setting or a weak from a strong law of large
numbers when the source requires that distinction. If a broad theorem contains
the needed special case, record why its conditions apply. Name matches with a
different sense remain unresolved.

An applicable match establishes available knowledge, not personal mastery.
Return the entry's `understanding` separately: `unknown`, `not-yet-understood`
or `understood`. Skip a tutorial on the basis of mastery only when the user has
stated understanding, while retaining the current use conditions. Surface
direct `pending_prerequisites` without recursively expanding their ancestry.
Finding a definition or a successful query never upgrades understanding. Never
generalize a concept's status to an entire discipline. An unavailable query is
not a missing concept, and a missing concept is not proof the user lacks the
surrounding subject. Do not query generic subject names as substitutes for
actual prerequisite uses.

### Identity classification for authoring

When an extractor asks whether candidates already exist, classify each one:

- `matched`: `agent resolve` returned `exact` or `alias`, and the entry's
  content has the same meaning and conditions; return its id;
- `ambiguous`: several plausible entries or senses remain; return ranked
  candidates with non-authoritative reasons;
- `unmatched`: no entry has this meaning; the extractor may author a new entry.

Spelling, translation and aliases from the candidate side are retrieval
evidence only. Inspect bounded definitions and defining conditions before
calling a lexical hit `matched`. Failed exact lookup is not enough to call a
candidate unmatched while plausible senses remain. Same-named paper-scoped
mechanisms keep separate entries unless their meaning has been reviewed as
equivalent.

## Return a compact handoff

Return one record per candidate: query, status, established entry id,
authoritative evidence, bounded content excerpts with their source and line
range, retrieval reasons, and caller action (`reuse`, `author-new`, or
`review`). Report operations used, result counts, ambiguity and omitted
context. Make no repository changes. Return personal understanding and direct
pending prerequisites separately from identity match and definition
availability.
