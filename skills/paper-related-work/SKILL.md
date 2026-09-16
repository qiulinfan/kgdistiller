---
name: paper-related-work
description: Explicit command only. Find cited predecessors, citing successors and bounded online discussion using fixed resource methods and parallel branches. Never activate from an ordinary natural-language research or paper-reading request.
disable-model-invocation: true
---

# Quick related work

Run only for an explicit `$paper-related-work` or `/paper-related-work` command.
Phrases such as "查找相关论文", "前作/后作", "看看审稿意见" and "网上怎么讨论"
alone do not activate this Skill. Match the user's language; preserve identifiers,
source titles and raw errors. Ordinary paper explanation is separate.

## Method first

Read [resource-methods.md](references/resource-methods.md). Reuse known paper
identity and links; do not pre-research all directions before dispatch. Give each
requested branch to `kgdistiller-related-work-scout` with the paper identity, the
absolute method-file path, assigned channels, language and common cutoff. Do not
rewrite the method, add seed-paper lists, or create extra subtasks.

| Branch | Assigned resource channels |
|---|---|
| Predecessors | Paper's original text (HTML first), own bibliography and citing passages |
| Successors | OpenAlex `cites` query and the bounded source check |
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

Keep one total 120-second budget from task start. Dispatch promptly; use the same
absolute retrieval cutoff around +75 seconds for all workers, leaving time to
answer by about +110 seconds. Cancel unfinished work at the cutoff and deliver
returned evidence with gaps. The clock is a backstop; fixed methods and immediate
resource exits do the work. Never reset budgets or wait for every branch to succeed.

## Answer once

A paper may have only its arXiv source and no public discussion. That is a normal
completed result, not a gap to fill. Never add forums, query variants or criticism
to make every direction nonempty. Report only what the bounded search found.

Return a flexible shortlist of up to eight predecessors and up to eight successors,
with direct sources; eight is a ceiling, not a quota. Rank the retrieved papers by
connection to the target, usefulness for understanding its research context, and
complementary coverage; citation count alone is not quality. Briefly state why
each paper is worth reading, without claiming a globally best or exhaustive list.
Keep online discussion to at most two useful findings. Any citing paper qualifies
as a successor; method inheritance is optional additional information, never a
filter for inclusion. Keep citation-index candidates distinct from passage-verified inheritance;
keep original review claims distinct from community opinions. A community report
is not an independently reproduced result. Preserve which resources were checked.

Answer directly in the user's language. Let length follow the number of useful
papers: normally one line per paper (title/link plus its connection or value), with
brief discussion and gaps. Avoid long summaries of every paper. On no findings,
give the resource gap and link without a
paper summary, speculative criticism or a follow-up permission question. Create
an artifact only if explicitly requested, within the same budget. Never start
distill/harvest, expand a personal graph, or import knowledge.
