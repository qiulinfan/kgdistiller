---
name: paper-related-work
description: Explicit command only. Find cited predecessors, citing successors and bounded online discussion using fixed resource methods and parallel branches. Never activate from an ordinary natural-language research or paper-reading request.
disable-model-invocation: true
---

# Related work

Run only for an explicit `$paper-related-work` or `/paper-related-work` command.
Phrases such as "查找相关论文", "前作/后作", "看看审稿意见" and "网上怎么讨论"
alone do not activate this Skill. Match the user's language; preserve identifiers,
source titles and raw errors. Ordinary paper explanation is separate.

## Method first

Read [resource-methods.md](references/resource-methods.md). Reuse known paper
identity and links; do not pre-research all directions before dispatch. Give each
requested branch to `kgdistiller-related-work-scout` with the paper identity, the
absolute method-file path, assigned channels and language. Do not
rewrite the method, add seed-paper lists, or create extra subtasks.

| Branch | Assigned resource channels |
|---|---|
| Predecessors | Paper's original text (HTML first), own bibliography and citing passages |
| Successors | OpenAlex citations plus one focused scholarly search; group by research relationship |
| Online discussion | At most two applicable sources: known public reviews, Hacker News, or readable Hugging Face Papers comments |

A broad request uses these three branches; a narrow request only uses the requested
branch. Public peer reviews belong to online discussion: use a known official
review entry or an explicit review request, passing
[quick-peer-reviews.md](references/quick-peer-reviews.md). No separate default
review hunt.
Use runtime-native parallel workers without recursion. The parent may own one
branch when capacity requires it. Do not queue late work. Claude's
scout tools exclude delegation, shell and writes; Codex's preset expresses the
same boundaries as instructions. Report actual execution, not assumed parallelism.

## Stop by resource result

A resource returning useful material is summarized immediately. Follow only the
bounded identity correction and discovery repair defined in the method file. An
unresolved/missing record ends with "not found in this resource". An inaccessible
resource ends with its access gap. Do not prove global nonexistence or add searches
beyond the prescribed paths. A worker timeout is unavailable/pending, not an empty
search. No account setup,
software installation, custom parsers, bulk downloads or source redistribution.

There is no default wall-clock deadline for research or synthesis. Do not start
countdown tasks or cancel a worker because an arbitrary duration has elapsed.
Let each branch finish its bounded research and explanation, then collect its
actual return. Treat an access error or failed worker as a concrete gap; do not
turn an unfinished synthesis into "no citations found". Respect user cancellation.

## Answer once

Wait for the launched branches to return or report a concrete failure before the
final synthesis. Use only actual returned evidence; never predict a worker's
result or fabricate a completion notification. Deliver one self-contained answer.

A paper may have only its arXiv source and no public discussion. That is a normal
completed result, not a gap to fill. Never add forums, query variants or criticism
to make every direction nonempty. Report only what the bounded search found.

Return a flexible shortlist of up to eight predecessors and up to eight successors,
with direct sources; eight is a ceiling, not a quota. Rank the retrieved papers by
connection to the target, usefulness for understanding its research context, and
complementary coverage; citation count alone is not quality. Briefly state why
each paper is worth reading, without claiming a globally best or exhaustive list.
For successors, follow the method file's relationship groups: core research
advances, evaluation/critical analysis, applications/transfer, and surveys/background.
Prioritize direct progress on the target's question; same-subfield membership is a
signal, not a gate. Eight is the total across groups, with no group quotas.
Keep online discussion to at most two useful findings. Any citing paper qualifies
as a successor; method inheritance is optional additional information, never a
filter for inclusion. Keep citation-index candidates distinct from passage-verified inheritance;
keep original review claims distinct from community opinions. A community report
is not an independently reproduced result. Preserve which resources were checked.

Answer directly in the user's language. Let length follow the number of useful
papers and the relationships worth explaining. Give each paper a title/link and
its connection or value; explain important successors in enough detail to show
what they change, test or extend, with source support. Simpler/background citations
can stay brief. Do not force every paper into one line to save time. On no findings,
give the resource gap and link without a
paper summary, speculative criticism or a follow-up permission question. Create
an artifact only if explicitly requested. Never start
distill/harvest, expand a personal graph, or import knowledge.
