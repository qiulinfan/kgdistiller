# OMP compiled knowledge tools

`integrations/omp/compiled_tools.ts` is a manually loaded OMP extension for an
explicitly supplied compiled library. It registers `kgd_search`, `kgd_get`,
`kgd_inventory`, `kgd_pack` and `submit_selection`. The first four operations are
read-only; submission writes one immutable selection artifact in the current
run directory. It never changes the library, graph or source documents.

The extension uses the existing OMP ExtensionAPI and its injected schema builder;
there is no additional JavaScript dependency or model choice. Its Python bridge
calls the provider-neutral `CompiledLibrary` API. The caller chooses the model,
enables the five tools and sets their approval policy through the configured OMP
runtime. Essential tool visibility does not itself enable or approve a tool.

## Run-local input

The caller supplies two regular JSON files in the existing run directory:

- `compiled-tools-config.json` has exactly `python_interpreter`, `library_path`,
  `byte_budget`, `reference_limit`, `search_limit` and `max_response_bytes`.
  Interpreter and library addresses are absolute. Limits are positive integers;
  the response bound must accommodate an explicit error envelope. The same byte
  bound applies to an input request. `byte_budget` is fixed for the run and cannot
  be overridden by a tool call.
- `questions.json` is a nonempty array of records containing only a unique,
  nonempty `qid` and literal nonempty `question`. These addresses route selections;
  they do not establish scientific identity or coverage.

`python_interpreter` must name an executable Python with this product installed.
The extension passes that exact lexical address to the process launcher, including
when it is a virtual-environment entry. It does not resolve the entry to its
underlying interpreter, install packages or switch interpreters on failure.

The bridge is invoked as `PYTHON -m kgdistiller.omp_compiled_tools --config CONFIG
--output RUN_DIRECTORY`, with one UTF-8 JSON request on stdin. There is no shell
command interpolation. The extension source is also shipped at
`kgdistiller/product/integrations/omp/compiled_tools.ts` in an installed package;
the Python module requires no sibling checkout or helper script.

## Evidence and submission boundaries

Get, inventory and packing preserve complete scientific fields, nested conditions,
formulae, evidence and source qualifications. Pack omissions stay explicit gaps.
Inventory describes compiled declarations and does not certify source-corpus
completeness. Search scores and successful tool execution provide no scientific
correctness or task-success verdict.

Only opaque source addresses in known `unresolved-source` gaps are hidden from
model-facing output. The gap reason remains, and a metadata-projection note
explains that any core byte count describes the original packet. Scientific
content is not projected or shortened. A response that cannot fit the caller's
bound fails explicitly; it never returns a truncated success.

Read and pack references must be distinct exact entries in the current supplied
node registry. Submission must cover every current question exactly once and in
its original order. Each `ranked` array has the same exact-reference and count
checks; `abstain` is Boolean and requires an empty array when true. Unknown
question addresses, duplicate entries and extra fields fail before any write.

The complete selection is created as `submitted-selection.json` using exclusive
creation. A second submission cannot replace the first. This artifact records
selected addresses; scientific support is evaluated separately against original
sources and the actual packed content.

The tool registration and bridge originate in the generic code of
`qiulinfan/kgdistiller-experiment`, commit
`cf2960559f520cade5ce00cf1734705bf226dd01`. No private library, question set,
prompt, plan or benchmark result is included.
