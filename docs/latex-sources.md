# Native LaTeX knowledge sources

LaTeX stays an identity authority. Only explicit `\kn{Name}` and
`\knref{Name}` markers create definitions or references. No document conversion,
environment declaration, heading, rendered label, or file order defines an ID.

## Source scanning

The dependency-free scanner keeps original offsets and line numbers while
excluding comments, supported verbatim/listing forms, inactive `\iffalse`
branches, and macro/environment declarations. It does not interpret arbitrary
conditionals, expand macros, load packages, or admit unregistered included files.

Statement ranges use matched environment stacks, including same-name nesting
and starred forms. Missing or mismatched supported statement ends stop sync.
Local `\newtheorem` declarations supply bounded ranges; standard names or
explicit standard titles supply kinds, and other declarations remain concepts.
These ranges bind definition digests and reference contexts. Unknown math macros
remain named tokens in searchable labels rather than disappearing. Unchanged
native marker spellings retain existing graph IDs when normalization improves.

When a registered source admits both `chapter.typ` and `chapter.tex` in the same
directory, discovery, synchronization and source-backed exports select only the
TeX authority. This choice does not depend on glob or file order. An explicit
`sync --file chapter.typ` selects its admitted TeX sibling too. A registry that
admits only Typst keeps using Typst; an unregistered TeX sibling is never added
implicitly. Typst-only and TeX-only documents, Markdown authorities, and equal
stems in different directories remain independent. Removing TeX exposes an
admitted Typst sibling again, including an incremental sync of the deleted path.
Transactional authority patches must target the selected authority; a shadowed
Typst file is not an ingestion target.

Synchronize before exporting an existing graph after adding a TeX sibling.
The switch removes the old Typst authority hash and references and keeps IDs
resolved by the existing authored names and identity registry. If a converted
marker has a different normalized name, register its explicit alias first:
for example, the original Typst `$L^(+)$ space` and native TeX `$L^{+}$ space`
normalize to different identity names. Their association must be recorded in
`.knowledge/identities.json`, using the existing ID and the converted normalized
name as an alias. Sync refuses to write a generation if an old active definition
cannot resolve to the same ID in its TeX sibling, rather than retaining an old
entry and creating another. Filenames and marker order establish no identity.

The generated Typst registry preserves the original rendering spellings from an
admitted Typst companion. This is a read-only projection: only explicit markers
that already resolve to existing IDs contribute names, and a definition must
belong to the paired TeX authority. References may contribute an authored spelling
for an already resolved active target. These names create no nodes, graph
properties or Typst authority hashes. Sync and static-site export use the same
mapping; static export reads only published companions and requires them to be
tracked source inputs. The node's primary authority, source format and graph
labels remain native TeX.

## Mathematical names

Plain names receive escaped text labels without installing a renderer. Rich
LaTeX names are rendered by the local `obsidian-latex-live` converter, using its
existing project macros and isolated MathJax pipeline. The graph stores passive
HTML/MathML in `properties.label_html` and original TeX in `source_name` and
`latex_name`. MathML requires neither injected script nor provider font CSS.
The graph engine validates label IDs, exact response coverage, and permitted
elements/attributes before writing a generation.

The default executable is `latex-live-export`, supplied by
`obsidian-latex-live/scripts/kgdistiller-export.mjs` after that checkout's npm
dependencies are installed. A machine may link that executable into its PATH.
For an explicit provider location, set `KGDISTILLER_LATEX_HTML_COMMAND` to a JSON
argument array, for example:

```sh
export KGDISTILLER_LATEX_HTML_COMMAND='["node","/absolute/path/obsidian-latex-live/scripts/kgdistiller-export.mjs"]'
```

No shell evaluates those arguments. The engine does not vendor the converter,
MathJax, a TeX engine, or a LaTeX-to-Typst adapter.

## Native document integration

Generate a TeX registry from the current synchronized graph:

```sh
kgdistiller export latex-registry --output .knowledge/build/knowledge-registry.tex
```

Input it in the native document preamble. It provides `\kn`, `\knref`,
`\kgdistillerNodeId` and `\kgdistillerNodeUrl`, and loads hyperref. It preserves
canonical names, aliases and authored LaTeX reference spellings. Name tokens
are compared through `detokenize`, never expanded to infer an identity. A first
definition establishes `kn-ID`; subsequent references use an existing local
target or the authority/graph URL. Unknown names stay readable with a warning.
This is an explicit derived export, not an extra authority or a change to the
transactional generation/portable four-file static bundle.

Export a complete native LaTeX document directly:

```sh
kgdistiller export latex notes/main.tex --output .knowledge/build/main.html
```

The provider copies only the document's bounded dependency closure to temporary
storage, renders through its existing exporter, and cleans up. Exact native
names map to graph IDs and URLs; definitions become `id="kn-ID" data-ql-kn="ID"`
and references carry `data-ql-ref="ID"`. Unknown or duplicate definitions fail.
Markers surround math in their names, e.g. `\kn{$\sigma$-algebra}`, rather than
appearing inside a math environment. Input authorities remain untouched, and a
changed authority aborts output installation. `--replace` authorizes replacing
an existing generated result.

Document rendering uses the provider's pdfLaTeX/XeLaTeX support and self-contained
HTML/assets; the current provider explicitly rejects LuaLaTeX. It does not use
Typst or Pandoc. The notes repository's `export_latex_web.py` calls this command
and wraps explicit fragments when necessary. Its separately requested
LaTeX-to-Typst migration tools remain independent.

## Converter protocol

One UTF-8 JSON request on stdin uses `latex-live-html-request-v1`:

- `operation: labels`: optional absolute `.tex` `source`, and
  `labels: [{id, latex}]`; returns exactly `labels: [{id, html}]`.
- `operation: document`: absolute `.tex` `source`, optional absolute
  `project_root`, `markers: [{name, id, url}]`, optional `engine`; returns a
  complete `html` and the provider's `report`.

The response uses `latex-live-html-result-v1` and the same operation. Errors and
progress belong on stderr; nonzero exit, invalid JSON, mismatched operation,
missing/duplicate labels, or unsafe label markup abort the operation.
