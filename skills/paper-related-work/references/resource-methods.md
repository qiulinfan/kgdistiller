# Bounded resource methods

Use only assigned channels. Distinguish identity resolution, retrieval and evidence:
a successful HTTP response or matching paper page is not yet a useful finding.
Use the finite paths below; no retry loops, mirror hunts or recursive expansion.
All steps share the parent's retrieval cutoff. Skip remaining steps at the cutoff.

## Public peer reviews (within online discussion)

Use only when an official review entry is already known or reviews were explicitly
requested; this consumes one of the discussion branch's two resource slots.
Use [quick-peer-reviews.md](quick-peer-reviews.md): official OpenReview notes or
the publisher's own public review file. Only original text supports review claims.
Record the submission/version reviewed. No public record is different from a
blocked resource; neither implies rejection.

## Original text / predecessors

For a known arXiv ID, open its versioned `/html/IDvN` directly. If the ID or full
text location is unknown, make one exact-title search scoped to arXiv/official
publication sources and verify title/authors. Read bibliography entries together
with their citing passages; an abstract or generated summary is not full text.

If HTML fails, check the already known official landing page once for its full-text
links. Use already available TeX next, otherwise one readable official PDF for the
relevant passages only. Do not start a TeX build, install a parser, or download a
corpus. State when a different version supplies the passages. Return up to eight
predecessors with specific cited uses, ranked by contribution to understanding the
target (e.g., method origins, theoretical support, important comparisons). Do not
fill the list with generic background or expand into those papers' references. Missing arXiv identity, unavailable HTML conversion,
unreadable full text, and no relevant passage are separate gaps. arXiv does not
provide HTML for every paper; never invent an arXiv ID from a similar title.

## Incoming citations / successors

Here, a successor means ANY paper citing the target. An indexed incoming-citation
record or a verified bibliographic citation qualifies; method adoption is not an
admission requirement. Include surveys, comparisons, criticism and background-only
citations. If its role is unknown, say "cites this paper; use not checked" rather
than dropping it. Similarity or an unverified search mention alone does not qualify.

Reuse verified arXiv IDs, formal publication DOI, title, authors and year. A
preprint DOI and publication DOI may resolve to different or missing records.
Use a known formal DOI for OpenAlex before a preprint DOI. Do not silently merge
versions or rename the target to a related paper. Two verified versions may be
searched together with their provenance retained.

**OpenAlex.** Resolve the known DOI via `/works/https://doi.org/DOI`; without a
usable DOI use `/works?filter=title.search:TITLE&per_page=5&select=id,title,doi,authorships,publication_year,cited_by_count,locations`.
The title-only filter remains supported (though documented as legacy); ordinary
`search=TITLE` also searches abstracts/full text, so its first result is not an
identity match. Compare title AND authors/identifiers across the small page.
On a missing DOI record or unmatched title, allow one identity correction using
a verified alternate publication title or an unambiguous paper name. No blind
query variants. At most two identity requests total, then stop if unresolved.

For up to two verified record IDs, fetch one page via
`/works?filter=cites:W_ID&per_page=20&sort=cited_by_count:desc&select=id,title,doi,publication_year,cited_by_count,primary_location`.
Use `cites:W1|W2` to combine two verified versions in that single request. Return
up to eight citing papers, prioritizing relevance to the target and useful variety
(e.g., extensions, applications, comparisons or surveys) within this small page.
Use only roles supported by the available evidence, and rank by more than citation
count; do not claim these are the best papers across the full citation graph. Do not filter
out valid citations because their method use is unknown. Neither the provider's
default order nor high citations establish a representative sample.
Empty results mean no incoming citations indexed for the checked records.

**One bounded source search.** If OpenAlex is unavailable/unresolved/empty, the
successor branch may run ONE exact-title web search. Discard the target's own
records from the returned results; do not block whole scholarly domains such as
arxiv.org or openreview.net, which also host citing papers. Inspect at most one
original citing paper (HTML preferred; readable official PDF accepted) for the
bibliographic entry and citing passage. This is a bounded way to recover a citation
missed by an index, not a new branch or permission to keep changing keywords.
A search snippet alone is an unverified lead. If OpenAlex candidates already suffice,
use that same one source-read allowance to verify the best candidate instead.

Report indexed citation vs source-verified citation vs passage-verified method use
accurately. A citation alone is not method inheritance. Similarity neighbors are
not successors. Never claim completeness or infer no successors from index gaps.

## Online discussion

Use at most two applicable resources in this branch, including public peer reviews
above. Prioritize a known official review entry when present, then Hacker News;
Hugging Face Papers is a supplement only when its comments are readable. An explicit
review-only request uses only the review resource. Do not search for review venues
just to fill a broad request. Reddit and Zhihu are excluded. No additional forums.

For community sources:

- **Hacker News:** one Algolia request
  `https://hn.algolia.com/api/v1/search?query=PAPER_NAME&hitsPerPage=8`.
  Use an unambiguous paper name plus distinctive topic words, or the title; verify
  the story URL/parent story links to this paper or its official project. Acronyms
  alone can collide. Open at most one matched thread via
  `https://hn.algolia.com/api/v1/items/STORY_ID` and extract at most two substantive
  technical comments. Cite `https://news.ycombinator.com/item?id=COMMENT_ID`.
  For oversized threads, use one bounded comments query instead of the item read:
  `/api/v1/search?tags=comment,story_STORY_ID&hitsPerPage=10`; acknowledge that this
  is a sample. Do not use general product news or stock-market chatter as research
  discussion, and do not follow outbound links recursively.
- **Hugging Face Papers:** if arXiv ID is known, open
  `https://huggingface.co/papers/ID` once and read the Community/comments section.
  The paper Markdown/API metadata may omit comments. If comments are not exposed
  by the available reader, report that gap; no custom scraper or login flow.
  When this reader is already known to omit HF comments, skip rather than repeat
  the same failed path across papers. Without a verified arXiv ID, skip this resource. Filter automated librarian
  recommendations, AI summaries, upvotes, link-only promotion and generic praise.

Human technical questions count as questions, not proven flaws. Report specific
views with attribution; community experience is not an independently reproduced
result. No discussion is a normal outcome, especially for papers with only an
arXiv release; end the branch without trying to fill it. A page with no substantive
comments is not a discussion hit. A query with
no matches says only that this query found none, not that discussion cannot exist.

## Compact return and evaluation

Return channel, status, a ranked shortlist of up to eight papers for each of
predecessors/successors, and at most two online-discussion findings, with direct
URLs and specific gaps. Use fewer when evidence or time warrants; never search
extra pages, invent roles or exceed the shared cutoff to reach eight. Keep statuses distinct: identity unresolved, access unavailable,
matched record with no indexed citations, lead only, and useful source content.
Do not pad missing channels with abstracts, biographies or guesses. In evaluations,
measure access success, identity matching and useful-content coverage separately;
report timing separately from these, including failed requests in timing.

Sources: [arXiv HTML coverage](https://info.arxiv.org/about/accessible_HTML.html),
[OpenReview notes](https://docs.openreview.net/how-to-guides/data-retrieval-and-modification/how-to-get-all-notes-for-submissions-reviews-rebuttals-etc),
[OpenAlex searching](https://help.openalex.org/api/searching/),
[OpenAlex citation recipes](https://help.openalex.org/how-to/api-recipes/),
[Hacker News search API](https://hn.algolia.com/api).
