# OMP compiled knowledge tools

`integrations/omp/compiled_tools.ts` is a manually loaded OMP extension for an
explicitly supplied compiled library. It registers `kgd_search`, `kgd_browse`, `kgd_get`,
`kgd_inventory`, `kgd_pack` and `submit_selection`. The first five operations are
read-only; submission writes one immutable selection artifact in the current
run directory. It never changes the library, graph or source documents.

The extension uses the existing OMP ExtensionAPI and its injected schema builder;
there is no additional JavaScript dependency or model choice. Its Python bridge
calls the provider-neutral `CompiledLibrary` API. The caller chooses the model,
enables the six tools and sets their approval policy through the configured OMP
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

Search and browse calls accept `qid` to route observations to one current
question. It is optional when the run contains a single question, in which case
that sole address is used. Runs containing multiple questions require an
explicit current `qid`. Both adapter and bridge validate the address before
retrieval; it is never passed to the library's semantic search or navigation.
Get, inventory and pack do not acquire a question address.

`kgd_browse` calls the existing `CompiledLibrary.browse(reference, kind=kind)`
API. Omit `reference` for a root overview listing the supplied source, layer and
term handles. Open one of those exact handles to inspect its branch. Optional
`kind: source|layer|term|node` disambiguates overlapping handles and requires a
reference. Source and layer branches return `entries`; term branches return
`senses`. A node browse returns the complete node entry. No identity is inferred
from a question address, heading, document order or branch membership.

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

Get and pack reference lists must contain distinct exact entries in the current supplied
node registry. Submission must cover every current question exactly once and in
its original order. Each `ranked` array has the same exact-reference and count
checks; `abstain` is Boolean and requires an empty array when true. Unknown
question addresses, duplicate entries and extra fields fail before any write.

In the OMP adapter, each ordered `ranked` array must have an earlier successful
`kgd_pack` preview in the current run. A preview qualifies only when the whole
response succeeds and its complete entries deliver every requested reference in
the same order. A budget omission remains visible in the returned packet but
does not qualify the omitted selection. Source, dependency and semantic gaps do
not invalidate this transport prerequisite or certify scientific completeness.
An empty or abstained selection also needs an empty-list preview. Multiple
questions may reuse one matching preview; packs do not acquire a question address.
This run-local check uses exact arrays, and does not change the neutral Python
core or direct bridge semantics.

The adapter also records successful search and tree observations separately for
each question in the current run. A non-abstained selection requires at least
one selected reference that was actually returned by that question's successful
`kgd_search`. Every selected reference must also have appeared in the `entries`
of a successful source or layer branch browse, or the `senses` of a successful
term branch browse, for that question. Tree expansion may supply additional
selected references beyond the search candidates. Root overviews, node browse,
get and inventory do not satisfy the selected-reference branch prerequisite.

Abstention requires a successful search, which may return no candidates, a
successful root overview for that question, and the existing empty-list complete
pack preview. Failed, cancelled or oversized responses never qualify as
observations or previews. Search candidates and tree visits cannot be reused
across questions; an exact pack preview can still be shared. State lasts only
for the current adapter instance and introduces no persistent receipts or
library changes. These guards prove that the declared observations occurred;
they do not prove that selected evidence is semantically complete or correct.

The complete selection is created as `submitted-selection.json` using exclusive
creation. A second submission cannot replace the first. This artifact records
selected addresses; scientific support is evaluated separately against original
sources and the actual packed content.

The tool registration and bridge originate in the generic code of
`qiulinfan/kgdistiller-experiment`, commit
`cf2960559f520cade5ce00cf1734705bf226dd01`. No private library, question set,
prompt, plan or benchmark result is included.
