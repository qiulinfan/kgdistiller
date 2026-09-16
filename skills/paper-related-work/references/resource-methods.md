# Bounded resource methods

Use only assigned channels. Distinguish identity resolution, retrieval and evidence:
a successful HTTP response or matching paper page is not yet a useful finding.
Use the finite paths below; no retry loops, mirror hunts or recursive expansion.
Research and synthesis have no default elapsed-time limit. Complete the assigned
source checks and explanation; finite source scope bounds the work.

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
Use `cites:W1|W2` to combine two verified versions in that single request. This
page is one discovery sample, not the final ranking; do not copy its citation-count
order into the shortlist. Empty results mean no incoming citations indexed for
the checked records.

**One focused scholarly search.** Alongside the citation-index request, run at most
ONE scholarly web search for the target's name/title plus its core research
question or mechanism (taken from the target, not an invented list of successors).
Look for work advancing or testing that research line, including less-cited recent
papers. If no such context is available, use the exact title to discover citations.
This search complements even a nonempty index page: high-count results can be
mostly cross-domain applications. Do not run separate searches for every group,
repeat query variants, or paginate to fill eight slots. Discard the target's own
records, not entire domains such as arxiv.org or openreview.net.

Merge and deduplicate candidates from both sources. An index-confirmed citing
paper remains eligible without reading its full text. For each shortlisted
successor that needs verification or explanation, read its accessible original
abstract and relevant citation/method passages (HTML preferred; readable official
PDF accepted). Focus on at most eight shortlisted papers, reusing already read
sources rather than expanding recursively into their references or successors.
A web-only candidate must have a verified citation before joining the confirmed
successor list; otherwise keep it as an unverified lead. Mark any inaccessible
source or unverified role accurately. Never assume a known-sounding successor
actually cites the target or add papers from memory to complete a group.

### Group by research relationship, then rank

Return at most eight distinct successors TOTAL across the following groups; show
only groups represented in the results, with no fixed quotas. Start with the core
research line rather than papers from whichever application field has most hits.

| Group, in usual reading order | What belongs here |
|---|---|
| Core research advances | Direct improvements, extensions, theoretical explanations or generalizations of the target's central question/mechanism. Same-subfield work usually belongs here when that direct connection is supported. |
| Evaluation and critical analysis | Reproduction, comparisons, counterexamples, limitations or systematic tests that change how the target should be understood or used. |
| Applications and cross-field transfer | Uses in a new task/domain where the central contribution is the application or adaptation. |
| Surveys and background citations | Work organizing the literature, or citing the target mainly as context. |

Within groups, prefer a direct connection to the target, substantive new insight,
and complementary coverage over repeated applications of the same idea. Use
citation count only as a secondary tie-breaker among comparably relevant papers;
low citation counts do not disqualify new core advances. Same venue, shared topic
words, and a broad label such as "statistical machine learning" do not by themselves
establish a close research relationship. Cross-field work that contributes a new
general mechanism or theory can rank in the first group; an especially useful
survey can be moved forward when it serves the user's reading question.

Assign one primary group per paper from the evidence actually read (abstract or
citing passage where available). A title can suggest an application area, but
cannot establish method inheritance. Mark provisional classifications accordingly.
Confirmed citations whose role is unclear remain eligible under "relationship not
checked"; uncertainty is not evidence of low value. Each selected paper gets a
link and a source-backed explanation of its connection/reading value. Important
advances may need several sentences; background citations can stay brief. Do not claim a
globally best eight or hide the candidate pool's coverage limits.

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
URLs and specific gaps. Use fewer when the evidence warrants; never search extra
pages or invent roles merely to reach eight. Keep statuses distinct: identity unresolved, access unavailable,
matched record with no indexed citations, lead only, and useful source content.
Do not pad missing channels with abstracts, biographies or guesses. In evaluations,
measure access success, identity matching and useful-content coverage separately;
report timing separately from these, including failed requests in timing.

Sources: [arXiv HTML coverage](https://info.arxiv.org/about/accessible_HTML.html),
[OpenReview notes](https://docs.openreview.net/how-to-guides/data-retrieval-and-modification/how-to-get-all-notes-for-submissions-reviews-rebuttals-etc),
[OpenAlex searching](https://help.openalex.org/api/searching/),
[OpenAlex citation recipes](https://help.openalex.org/how-to/api-recipes/),
[Hacker News search API](https://hn.algolia.com/api).
