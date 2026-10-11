# Proposal and CLI contract

The helper supplies mechanical persistence, not a semantic grouping algorithm.
The agent reads this Skill and chooses topics/summaries/links. `context` reads
accepted records from all available registered bases; `apply --base` confines
changes to navigation records of that base and its selected navigation source.
Foreign participants must already be accepted. When creating shared topics
in several bases, accept the topic in its home base first, reread context,
then add foreign organization links. There is no default base.

`init --base B [--source PATH]` registers the guide and the optional finite
navigation profile. `context --base B [--source PATH] [--changed UID ...]`
returns `base`, `source`, `type`, `navigation`, `guidance`, `records`,
`protected`, `required_uids`, `diagnostics` and `snapshot`.

Read `records` for reasoning. It contains `uid`, `label`, `kind`, `class`,
`gloss`, `navigation` (`topic`, `organization`, `leaf`) and role-bound `out`
links. `snapshot` holds exact configuration/profile/guide/navigation text
and accepted file stat pairs; it is an ephemeral stale-read guard, not a
receipt or an authority. Do not change it. The home and the base's `.knowledge`
tree contain no copy of context or proposal JSON.

An executable proposal has these keys. Labels below are illustrative, not
product topic vocabulary:

```json
{
  "scope": "local",
  "reason": "New accepted records reveal two distinct uses of the previous broad entrance.",
  "topics": [
    {"id": "topic-shared-methods", "label": "Shared methods", "summary": "Routes to methods used in both theory and experiments.", "aliases": []}
  ],
  "organizations": [
    {"id": "route-shared-methods", "label": "Methods entrance", "summary": "Choose the method by its assumptions and use.", "parents": ["B:topic-shared-methods"], "children": ["B:existing-method", "other:existing-result"]}
  ],
  "retire": []
}
```

`topics` and `organizations` are upserts; omitted existing records are kept.
`scope: global` requires coverage of all accepted scientific records in every
available base, even when the generated topics/relations are authored in one
selected base. Thus one global proposal can organize multiple vaults into a
single semantic DAG; storage ownership is not a topic partition. To update
existing navigation records owned by several sources, apply each source's
scoped proposal sequentially with a fresh context; foreign new topics must be
accepted before foreign links can target them.
`context --changed` accepts scientific UIDs from any registered base, regardless
of the guide's write base. During initial compilation, local batches can grow
the cumulative graph while uncovered future batches remain globally searchable.
An existing parent may have several organization records, so adding a route
does not require reprinting a long old participant list. A final context with
empty `--changed` and `{scope: global, reason: ..., topics: [], organizations:
[], retire: []}` verifies global cumulative coverage without repeating it in
the proposal. Topic UIDs and summaries remain stable unless the semantic
reorganization actually changes them.
All participant UIDs use the exact canonical base/id spelling returned in
context or by CLI reads, without `.md` suffixes or display/path syntax. A local link is rendered without its
base prefix in the stored Markdown, per the existing link grammar. `retire`
contains full UIDs of obsolete owned navigation records. New ids are readable
slugs in the existing record grammar; scientific ids cannot be used for an
upsert. A topic cannot silently become an organization relation. Names,
depth, branching and the number of objects are chosen by the agent.

Summaries are non-empty single paragraphs of at most 600 characters. Put the
most useful routing decision in the first sentence: `gloss` is the existing
read-time first-sentence projection, capped at 240 characters. Preserve
existing aliases when still useful. `understanding` is never generated; a
manually changed understanding value protects that navigation record too.

The generated guide names each compiler decision and its participants as
plain text. New/changed records quote the exact appended statement and cite
its line. This preserves unaffected evidence while allowing split/merge and
retirement. The guide discloses that organization is agent-authored. It is
not parsed as a directory and does not become a second tree store: current
records and their declared roles determine navigation.

`apply --base B --context FILE --proposal FILE [--source PATH] [--dry-run]`
returns `created`, `updated`, `retired`, `scope`, `reason` and `metrics`.
A dry run makes no writes. A successful write additionally returns the CLI
`receipt`, `check` and `index` reports. The same proposal after a fresh context
is idempotent: identical upserts leave existing bytes/evidence unchanged.
Keep the helper's completed output in caller-owned transient storage if a
later CLI phase fails; interrupted changes are not a hidden transaction or
an accepted scientific update draft.

`kgd navigate [UID ...] [--base B]... [--limit 40] [--offset 0]` has bounded
top-level `records`: roots when UIDs are omitted, otherwise direct projected
children, deduplicated by full UID. Each summary carries `navigation`,
`child_count` and `parent_count`. `starts` summarizes requested existing UIDs;
`relations` summarizes the actual organization records used for this page;
`edges` are parent/child/relation pointers, not delivered scientific records.
`truncated` counts additional edge/relation pointers; `page.next_offset`
continues the child page. `profiles` discovers each registered source's kind
and role conventions. `diagnostics` reports cycle residuals (cycle members
and downstream topics), malformed roles, dangling links, empty topics and
uncovered scientific leaves, with totals and bounded examples. `lag` retains
the ordinary retrieval contract. A scoped view treats topics whose parents
are outside scope as entrances. Cross-base children remain visible in the
global view. No source syntax, label-based identity merging or scientific
neighbors are used to infer children.

For a concrete isolated smoke run, execute
`python scripts/smoke_navigation.py` from the product checkout after installing
its CLI. It makes temporary homes, accepts scientific fixture records,
applies a hierarchy and a growth reorganization through this helper, calls
the real CLI to navigate shared cross-base leaves, and verifies scientific
bytes/UIDs plus index rebuild. It invokes no semantic model; experiments must
also run the Skill's real agent to evaluate generated directory quality.
