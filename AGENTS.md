# kgdistiller agent guidance

- Keep the deterministic core provider-neutral. Model-specific behavior belongs
  in Agent skills or adapters.
- Never infer graph identity from document order, headings, or keyword
  co-occurrence. Only explicit source markers define knowledge nodes.
- Preserve user-authored markers and require evidence for semantic relations.
- The local server must bind to `127.0.0.1` by default and prevent path traversal.
- Maintain compatibility with all three authority formats: Markdown, Typst, and
  LaTeX.
- Run the complete unit test suite and package build for implementation changes.
- Do not add user knowledge data, credentials, generated graphs, or model keys to
  this repository.
