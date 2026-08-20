# Long-term roadmap

These items are intentionally deferred. They record product and research
directions without committing the current implementation to a design.

## Product presentation

- [ ] Design and build a polished public website that explains kgdistiller's
  purpose, source-grounded graph model, workflows, and integrations through
  clear examples and an approachable visual identity.

## Obsidian and Typst authoring

- [ ] Build an Obsidian plugin for first-class Typst authoring, inspired by
  Tinymist: provide live document preview while adding the native `.typ`
  editing experience Obsidian lacks, including syntax support, diagnostics,
  completion, navigation, and vault-aware file and link integration.

## Retrieval and RAG research

- [ ] Review kgdistiller's current hybrid retrieval and RAG method end to end,
  including identity resolution, lexical retrieval, graph expansion, context
  packing, ranking, provenance, evaluation, and failure behavior.
- [ ] Review and compare representative RAG families and systems, including
  conventional RAG, LightRAG, GraphRAG, and PageIndex. Document their different
  assumptions, indexing and retrieval models, strengths, costs, and failure
  modes, then use that comparison to propose evidence-backed improvements to
  kgdistiller's retrieval architecture.
