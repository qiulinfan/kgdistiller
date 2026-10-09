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
directory, discovery and synchronization select only the TeX authority. This choice does not depend on glob or file order. An explicit
`sync --file chapter.typ` selects its admitted TeX sibling too. A registry that
admits only Typst keeps using Typst; an unregistered TeX sibling is never added
implicitly. Typst-only and TeX-only documents, Markdown authorities, and equal
stems in different directories remain independent. Removing TeX exposes an
admitted Typst sibling again, including an incremental sync of the deleted path.
Transactional authority patches must target the selected authority; a shadowed
Typst file is not an ingestion target.

Synchronize an existing graph after adding a TeX sibling.
The switch removes the old Typst authority hash and references and keeps IDs
resolved by the existing authored names and identity registry. If a converted
marker has a different normalized name, register its explicit alias first:
for example, the original Typst `$L^(+)$ space` and native TeX `$L^{+}$ space`
normalize to different identity names. Their association must be recorded in
`.knowledge/identities.json`, using the existing ID and the converted normalized
name as an alias. Sync refuses to write a generation if an old active definition
cannot resolve to the same ID in its TeX sibling, rather than retaining an old
entry and creating another. Filenames and marker order establish no identity.
