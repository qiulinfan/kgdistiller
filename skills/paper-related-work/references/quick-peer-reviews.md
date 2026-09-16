# Peer-review lookup

Use at most one discovery search and two original-source fetches, including failed
attempts. Finish when these sources have answered the question or yielded a clear
gap; there is no elapsed-time cutoff.

1. Reuse the supplied title and official forum ID; do not rediscover known
   metadata. With both an OpenReview ID and title, fetch this public original-note
   search first: `https://api2.openreview.net/notes/search?query=URL_ENCODED_TITLE&limit=20`.
   Keep only notes whose forum matches the requested ID and invitations identify
   official reviews/meta-review or linked author responses. Ask the fetch tool
   for at most two concern/response pairs plus note IDs, not full note contents.
2. If no usable source is known, perform one discovery search. Read at most two
   original sources in total. An official forum or publisher review-file link is
   the fallback; do not spend both fetches on already blocked forum/notes routes.
   No endpoint probing, archives, aggregators, parser code or tool installation.
   A PDF-only unreadable review is an access gap.
3. Return at most two material reviewer concerns and an author response only if
   it is present in the inspected source. Link the original notes. A known decision
   can be stated briefly with its source; do not collect score histories or all
   evaluation tables. Label initial, predicted and confirmed scores separately
   if a score is necessary. Public comments and editorials are not formal reviews.

Do not add the paper abstract, speculate about likely objections, substitute
secondary commentary, or ask whether to continue searching. If original review
text was not retrieved, say "原始评审未取得" (in the user's
language), give the discovered official link and stop. An abstract, decision label,
search snippet or third-party summary alone cannot justify reviewer opinions.
Do not infer rejection from an access failure. Do not keep searching to fill two
findings. Return text only; no files and no further agents.
