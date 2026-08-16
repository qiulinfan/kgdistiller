---
name: distill-paper-knowledge
description: Turn a validated research-paper Markdown package into an isolated source-grounded candidate graph, align candidates through federated Vault recall, and produce a review handoff without mutating personal knowledge.
---

# Distill paper knowledge

Build an isolated paper graph and conservative alignment report. Reading,
summarizing, or aligning a paper never authorizes import.

Read
[references/research-paper-contract.md](references/research-paper-contract.md)
before distillation.

## Workflow

1. Accept only a complete, validated, source-traceable paper package from
   `$extract-paper-markdown` or an equivalent reviewed acquisition. Stop when
   identity, canonical text, equations, references, or source locations are
   incomplete.

2. Author a bounded `qlkg-candidate-graph-v2` candidate inventory in the paper
   namespace. Keep paper
   concepts, claims, definitions, methods, results, limitations, and relations
   tied to exact package headings/lines and source provenance. Do not infer
   identity from section order or keyword co-occurrence.

3. Build and validate an isolated candidate graph:

   ```sh
   kgdistiller candidate build paper.candidate.json --output paper.snapshot.json
   kgdistiller candidate validate paper.snapshot.json
   ```

   Retain both the candidate graph and deterministic snapshot as review
   artifacts, never as a native Vault generation.

4. Ask `$query-kgdistiller` for one bounded federated snapshot and batch all
   candidate canonical names/aliases through `recall resolve`. Use taxonomy,
   lexical, and graph lanes only to retrieve further candidates. Preserve
   `vault_id:node_id` handles and all ambiguous/missing outcomes.

5. Classify each paper candidate for review:

   - `reuse`: exact or reviewed-alias identity in a selected Vault;
   - `add`: source supports a distinct candidate for possible import;
   - `update`: source may enrich an existing qualified identity;
   - `reject`: not suitable for personal knowledge;
   - `defer`: identity, evidence, or scope remains unresolved.

   A lexical or graph neighbor is never automatically `reuse`.

6. Compare candidate relations against selected qualified endpoints. Record
   whether the paper directly supports each typed relation; do not convert
   document order, citation, co-occurrence, or transitive reachability into an
   edge.

7. Return the isolated candidate graph plus alignment/review handoff. If the
   user later selects candidates and one target Vault, hand them to
   `$import-paper-knowledge`; do not create note patches here.

## Boundaries

- Do not register a Vault, capture a personal source, write native notes,
  modify a graph, or call ingest.
- Keep paper candidate IDs and personal Vault-qualified handles in separate
  namespaces.
- Do not read raw Vault graph shards; use federated recall.
- Do not import an entire paper by default. Selection and target-Vault choice
  require explicit user review.
- Legacy paper alignment artifacts may be inspected only when explicitly
  selected; do not relabel them as a native recall report or ingest request.
- Match user-facing explanations, prompts, and handoffs to the user's language
  unless the user requests another language. Keep commands, identifiers,
  schema keys, action codes, and raw errors unchanged.

## Handoff

Return paper/package identity and digest, candidate-graph and snapshot
schemas/digests/counts, source coverage, federated and per-Vault generations,
qualified matches, candidate dispositions, relation findings,
ambiguity/omissions, and the exact subset awaiting user selection. State that
no personal Vault was mutated.
