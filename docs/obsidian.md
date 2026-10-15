# Obsidian preview and adjustment

A base is normally one Obsidian vault, and its knowledge lives in the vault's
hidden `.knowledge/` folder: accepted records in `entries/`, proposed records in
`drafts/` and generated def/pending sheets in `sheets/`. Obsidian itself is the
editor. The kgdistiller plugin adds two things: it lets Obsidian index the
hidden folder, and, once a kgd executable is configured, one sidebar looks
knowledge up in every registered base, for a selection, a typed query or the
file open in the editor, with the record in focus and a typed graph of its
neighbours. Its optional agent runs inside the plugin and calls model and
search APIs directly. `kgd check` stays the validator; Capture writes only
drafts admitted by `kgd accept`.

## Native-first editing

`kgd obsidian install` (see [Installation](#installation)) turns on the
plugin's hidden-folder indexing, so `.knowledge/**` joins Obsidian's vault and
metadata cache like any other folder. Then:

- the Properties panel edits `label`, `aliases`, `understanding`, `requires`
  and every role list, with link autocomplete;
- Backlinks groups a record's incoming links by role name, because every role
  is a top-level list property;
- renaming a record file rewrites the local links that point to it;
- drafts and sheets open in place, so a sheet's draft checkboxes are ticked in
  the ordinary editor before `kgd harvest`.

After any edit, run `kgd check` (and `kgd index` to refresh retrieval).

The plugin is desktop only: its manifest sets `isDesktopOnly`, so Obsidian
never loads it on a phone or tablet. Mobile is out of scope, because Obsidian
Sync does not sync dot folders and hidden indexing needs the desktop
filesystem adapter.

## Lookup

The **kgdistiller Lookup** view is the plugin's one sidebar; the ribbon icon
**Open kgdistiller** opens it with the cursor in its search box. Select text
in any view and hold the command key (⌘ on macOS, Ctrl elsewhere) for 0.3 s,
run **Look up selection**, or type into the search box at the top of the view
and press Enter (**Search knowledge** puts the cursor there). The selection,
with whitespace collapsed and cut at 500 characters, is looked up in every
registered base, and the view opens in the right sidebar without taking
focus. A press starts at most one lookup, whatever keydown repeats the system
sends. Pressing another key or a mouse button, or releasing the key, cancels
the hold first, so shortcuts such as ⌘C and ⌘-click never start a lookup, and
leaving the window forgets the press. Without a selection, or without a configured kgd executable, a hold
does nothing and the command says what is missing.

The view shows, from the top:

- **Same name**: the records whose label or alias has the selection's name key
  (the senses of `kg_resolve`). A shared name is not the same concept, so they
  are candidates to compare.
- **Pending**: the records that use the name as a pending term.
- The record in focus (the first name match, otherwise the first search
  result, or any record clicked):
  - its kind, base, source and lines, understanding and epistemic label;
  - **Neighbours within 3 steps**: a small typed graph of the records within
    three concept steps, from `kg_neighbors --dir both --depth 6`. Relations
    are edges: a relation with exactly two link values, both in roles, is one
    edge labelled with its kind, and one with more values or with `requires`
    links is a diamond joining them, its participants one step apart. A
    relation that another record links to, and a relation in focus, remain
    diamonds with their role edges. A relation about a single record is no
    edge and is listed under About it instead; pending terms are left out.
    Node records are circles, each kind taking a colour from a fixed
    twelve-colour palette by its position among the drawn kinds sorted;
    `requires` links are dashed and count as one step, and a missing record
    is red. The record in focus is larger, with an accent ring. At most the 40
    nearest records are drawn; clicking a record or an edge puts it in focus.
    Over the graph the wheel zooms, elsewhere it scrolls the panel. A
    collapsible **Legend** under the graph lists these styles;
  - its prose and Evidence rendered as in the editor and its links by role;
  - **About it**: the relations it takes part in, grouped by kind, with its
    role in each;
  - **Built on it**: the records whose `requires` lists it;
  - its `requires` chain three levels deep
    (`kg_neighbors --role requires --dir out --depth 3`), each record expanded
    once and pending terms as leaves.
- **Related**: the other records among the first ten of `kg_search`, with each
  lane's rank and the first line of the gloss.

While the view is open it follows the file open in the editor, when the vault
is a registered base (its root is the base's root in `kgd base list`):

- an accepted record `.knowledge/entries/<id>.md` comes into focus;
- a source, or its sheet `.knowledge/sheets/<source>.md`, lists under **In
  this source** the records the source defines, by kind and then line, from
  `kg_browse base:source`; the record in focus stays, and a click focuses
  another;
- a draft, any other file under `.knowledge/`, and a note that defines no
  indexed record leave the view as it is.

The later action wins: a selection or a search started while an opened file
is being looked up replaces it, and an opened source replaces an earlier
lookup's results.

There is no view of the whole graph: a graph of every record is too large to
read, so the graph is always the neighbourhood of the record in focus.

Record ids and uids are never shown: they are addresses for programs, not
knowledge.
- **Open record** and **Open source** open the file in this vault when the
  record's base is this vault, and otherwise through an `obsidian://open?path=`
  link in the vault that holds it.
- When a result reports lag, **Run kgd index** runs `kgd index` and repeats the
  lookup, or lists the source again. Records just edited or captured appear
  once the index has them.

Settings, under **Lookup**:

- **kgd executable**: the absolute path of `kgd`, since Obsidian started from
  the Dock does not see the shell's `PATH`. Empty, the default, turns lookups
  off.
- **Knowledge home**: the `KGDISTILLER_HOME` that lookups use; empty keeps
  kgd's default `~/.knowledge`. Plugin settings belong to the vault, so a test
  vault can point at a scratch home.

The plugin starts `kgd mcp` on the first lookup and keeps it running, so the
embedding model loads once (about 4 s for `BAAI/bge-m3` on Apple silicon) and
later searches take tens of milliseconds. It stops the server after 15 minutes
without a lookup, when either setting changes and when the plugin unloads. It
also runs `kgd base list` once per server to locate the bases, and `kgd index`
only from the button. Every run gets `HF_HUB_OFFLINE=1`: vectors exist only
after a local `kgd index` loaded the model, so the model is in the local cache.
These processes read the home, the database and the registered bases; the
plugin itself opens neither the home nor the database, and lookups write no
knowledge.

## Agent

A small agent built into the plugin adds four things to the lookup sidebar:
a judgment of the selection, **Capture**, **Find sources**, and an answer to
whatever is typed into the search box. Nothing is written until the owner
presses Capture or runs **Capture selection**.

The harness runs inside the plugin (`agent.ts`). Model requests go directly
from `model.ts` to the Chat Completions API of the model chosen in the
sidebar, at its provider, and search tools call
Exa's hosted MCP server (`https://mcp.exa.ai/mcp`) from `web.ts`. No OMP, external
agent executable, gateway process, generated extension or temporary search
file is involved. Local knowledge still uses the separately installed `kgd`
engine through its resident MCP server and admission commands.

A model request that the service answers with HTTP 503 (busy) or 429 (too
many requests) is sent again after 1 s and then after 2 s, each time with the
current settings. When the third attempt is refused too, the sidebar gives
the status with the service's own explanation when it sent one, since a 429
also means an exhausted balance or plan: Zhipu then answers code 1113,
"余额不足或无可用资源包,请充值。" Without an explanation it says the model
service is busy and suggests trying again or choosing another model. Any
other failure, a timeout included, is reported at once. On 2026-10-14
DeepSeek answered 503 "Service is too busy" to some requests and left most of
ten others unanswered for over a minute.

The plugin owns prompts, tool execution, concurrent read-only calls, bounded
turns and answer validation. Standard Chat Completions requests carry
`reasoning_effort`; the DeepSeek format maps thinking controls, preserves
`reasoning_content` across tool rounds and uses `auto` tool choice while
thinking (DeepSeek rejects forced choices then). A forced turn offers only
its named tool in that mode, and the harness rejects another tool's call.
API keys are selected or created with Obsidian's `SecretComponent` and read
from `app.secretStorage`; plugin `data.json` stores only secret identifiers.
It never imports OMP credentials or role aliases. Existing installations must
configure the new API endpoints, actual model ID and secret references.

The desktop HTTP client reuses connections, limits JSON responses to 8 MiB,
and cancels requests after 120 seconds for a model or 60 seconds for search.
Changing API settings cancels pending API requests; unloading closes the
client. HTTPS is required except for loopback HTTP services. Redirects are
not followed, so credentials stay with the configured endpoint. A failed
request shows the service's own explanation from a JSON error body
(`error.message`, an `error` string, `message` or `detail`) on one line of at
most 300 characters, with the request's key replaced by `[key]`; any other
error body is not displayed. API requests are not retried implicitly.
Missing search configuration is reported as a tool error; local knowledge
queries remain available.

The timing and quality observations below were measured with the earlier
OMP integration. They have not been remeasured for the direct API transport.
The direct transport is covered by local HTTP integration tests, including a
complete model → search → cited-answer round; these use synthetic keys and
make no calls to paid services.

**Judgment.** After the deterministic results, one call compares the
selection, read in the lines around it, with the records sharing its name and
the search's first results, and shows **Recorded**, **Possibly** or **Not
recorded** with one or two sentences on how the selection connects to them.
The model first says whether the selection is a concept or a statement, and a
match must have that class: a concept is a node, a statement a relation, and a
record that only mentions the selection is related, not the same. A matched
record is put in focus unless the owner has focused another one. With the
model's reasoning off, a judgment takes about 1.5 s on DeepSeek V4 Flash.

**Capture** files the selection as one record by the rules of
[the knowledge model](model.md) that the capture-kgdistiller Skill follows:

- The selection comes with its source lines: from the editor directly, and in
  reading view by finding its text in the source as reading view shows it,
  inside the selection's block when the text occurs more than once.
- The plugin prepares everything deterministic: `kgd sheet SOURCE --json` for
  the document type, the records already citing the source and the base, the
  numbered paragraph around the selection with the nearest heading, the first
  `kg_search` results for the selection, and `kg_resolve` of the
  Latin-script names in it.
- The model may call `lookup` once for other names and phrases, then calls
  `submit` with what it takes the item to be, the decision (new, unchanged,
  superseding, conflicting, or borrowed for a concept the source only uses or
  credits to other work) and, for a record, its label, kind, roles, requires,
  statement and evidence quotes with their lines.
- The plugin refuses kinds and roles the type lacks, links to records the
  model was not shown, and values that are neither links nor terms. It looks
  up every name the record would write (label, aliases and plain terms) and
  returns any recorded concept the model had not seen, so a term is looked up
  before it is written as pending. It drops a relation's aliases.
- Evidence holds only the source's text. A quote is kept when it is verbatim
  in its lines after collapsing whitespace, as `kgd check` compares; moved to
  its only occurrence in the source when the lines were wrong; and replaced
  by the source's own text when an alignment inside the cited lines is at
  least 90% similar. Otherwise the model is shown the cited lines to copy.
- A new record's id is its label folded as kgd folds names, with runs of
  other characters joined by hyphens, and numbered when an existing id folds
  the same. `integrations/obsidian/src/case-folding.json` lists every
  character whose Python `str.casefold` differs from its lowercase mapping,
  and the Python suite checks it against `unicodedata`.
- A new record is written as a draft with every frontmatter value a quoted
  string, admitted with `kgd accept` and indexed with `kgd index --no-embed`;
  `kgd accept`'s refusal returns to the model. Unchanged and borrowed write
  nothing. Superseding and conflicting show the proposed record beside the
  recorded one and write nothing yet: the in-place overwrite of a more general
  or corrected definition, relocation, and the linking of pending uses remain
  the capture Skill's.
- The sidebar reports the decision, the record and one line from the agent,
  with a notice when the capture ends. Capture asks for low reasoning: on
  eleven test selections from the test papers and notes it decided every one
  as expected in two runs, with a median of about 6 s and the slowest near
  20 s on DeepSeek V4 Flash, usually in one model call. Without reasoning the
  median was 4 s, but a concept the paper credits to earlier work was filed as
  its own.

**Find sources**, for an unrecorded or borrowed term, finds the work that
introduced it and the Wikipedia article that explains it. The sidebar lists at
once the works the selected lines cite, read from the source's own reference
list (block ids `^reference-N`). Then, at the same time, one web search
through Exa (as for Search below) asks who first introduced the
term, and Wikipedia gives the article the term's name leads to, read at the
section a redirect points into (masked autoencoder is the Masked Autoencoder
section of Vision transformer), and the first three articles its search finds.
One model call at low reasoning then judges both. It picks the originating
work among the source being read (its title and opening lines), the
references the selected lines cite and the first eight web results, or none,
as for a general term with no single origin or when the origin is not among
them; and the Wikipedia article that explains the term as the text uses it,
even under another name (latent representation is explained by Latent
space), or none, since a name or a search also finds other things of the same
name (BEiT finds Beit Shemesh, and linear probing the hash-table method). It
says why in one sentence, and the plugin refuses a choice that names no
candidate. A web search or Wikipedia that fails leaves the other's candidates
to judge, and the sidebar says which failed. The work is shown with its title
as the candidate gives it (the model's title when the candidate's text holds
it) and, when it has an arXiv link of its own, as its arXiv page with the year
its arXiv id gives. Only a work without such a link is searched on arXiv by
title, once and at least three seconds after the last search, as arXiv's terms
ask, since arXiv's API refuses clients that ask too often; a work arXiv does
not hold says so and keeps its own link. The article is shown with its opening
sentences, the Chinese article and the first paper its text cites. On nine
test terms from the MAE paper and the notes, the last round picked as
expected: BEiT, block-wise masking and MoCo v3 through the references the
passage cites, masked autoencoder as the paper being read, denoising
autoencoder as its reference (not on arXiv), variational autoencoder as
Auto-Encoding Variational Bayes, and none for latent representation, partial
fine-tuning and linear probing; latent representation was explained by
Latent space in five runs out of five, and every same-named article was left
out. A lookup took about 6 to 9 s on DeepSeek V4 Flash, a hard term up to
about a minute. With the plugin's own harness, the direct DeepSeek API
(`deepseek-flash`) and Exa's keyless search sending the origin query as
written, one round of the same nine terms picked eight as expected; linear
probing picked the paper on linear classifier probes (arXiv 1610.01644)
instead of none. The web search took 0.6 to 1.8 s, the judgment 3 to 22 s. It
sends the term to Exa,
Wikipedia and arXiv only when pressed, naming the plugin in its User-Agent as
Wikimedia asks; Wikipedia's text is shown with its CC BY-SA 4.0 notice. A
borrowed capture runs it by itself.

**Search** answers what is typed into the search box, from the knowledge
base first and from papers and the web for what it does not hold. The
deterministic results appear as for a selection; the agent starts from the
first search results, the records named in the question and, when the active
note is a registered source, that source and its document type (from
`kgd sheet --json`, which writes nothing). It reads more through read-only
tools, called together where they are independent: `kg_search`, `kg_get` and
`kg_neighbors` through `kgd mcp`; `paper_search`, which searches arXiv for
papers that may answer the question and keeps only arXiv pages; and
`web_search`. Both web tools make one direct HTTP POST to Exa's hosted MCP
server: a JSON-RPC `tools/call` of its `web_search_exa` tool with the query
normalized onto one line and `numResults: 8`, accepting JSON or server-sent
events. Without a key it searches at Exa's free rate limits, which Exa does
not publish; a 429 is reported as that limit. Search returns results directly
without another model call. Each result supplies its title, HTTP(S) URL and at
most 400 characters of its highlights to the model. Duplicate arXiv mirror
URLs are collapsed. A paper search adds `site:arxiv.org` to the query, which
Exa honours, and keeps the results that are arXiv papers. While the owner reads a registered source,
which is research material, the agent is offered the paper search only, so
an answer cites papers rather than encyclopedia or tutorial pages. Each step
shows in the sidebar as it runs. The answer cites records as `[[uid]]` and
papers and web pages as `[Wn]`, and every citation must be one the tools
returned; the sidebar shows them as K1, K2, … (a click puts the record in
focus) and W1, W2, … (links to the pages), with the cited records, papers
and other pages listed apart under **From the knowledge base**, **Papers** and
**From the web**. A paper's PDF, HTML or mirror page is linked as its arXiv
abstract page. The answer keeps the knowledge base and the web apart and says
so when the knowledge base holds nothing. A model that writes its answer as
text instead of calling the answer tool has still answered, and the sixth
model call must answer. On five research questions asked while reading the
MAE paper, answers took 4.2 to 15.4 s with the direct DeepSeek API
(`deepseek-flash`, reasoning off) and Exa's keyless search, against 5.5 to
11 s earlier through OMP, and every web source they cited was an arXiv paper.
An Exa search took 0.5 to 2 s.

Settings, under **Model providers**, hold the services the agent's models
come from, one at a time: **Provider** picks the provider to change from a
list, with **Add** and **Remove** beside it, so the section stays one
provider long however many there are. **Add** asks for the new provider's
service and name, suggesting a name after the service until one is typed,
and adds nothing until both are set and the name is free. A provider has:

- **Service**: its kind. A hosted service gives the base URL and the request
  format, so neither is typed: DeepSeek (`https://api.deepseek.com`, DeepSeek
  format), OpenAI, OpenRouter, SiliconFlow, Moonshot (Kimi), Zhipu (GLM),
  Alibaba Qwen (DashScope), Volcengine Doubao (Ark), Groq, Mistral, xAI,
  MiniMax, StepFun and Together AI, each of whose `/models` and
  `/chat/completions` answered on 2026-10-15. Ollama and LM Studio run
  locally and take their default address unless **Base URL** gives another;
  **Other OpenAI-compatible service** takes its own **Base URL** (the URL
  that `/chat/completions` and `/models` follow) and **API format**.
  Changing the service keeps the provider's name.
- **Name**: shown in the sidebar's model list. No two providers share a name;
  a rename the sidebar's choice used follows the provider, and **Remove**
  deletes the provider, clearing the choice when it was in use.
- **API format**, only for another service: **Chat Completions** for a
  service accepting `reasoning_effort` (`none` and `low`), or **DeepSeek** for
  its thinking and tool-choice rules. Every hosted service but DeepSeek uses
  Chat Completions, so it too must accept `reasoning_effort`.
- **API key**: select or add a secret in Obsidian SecretStorage. A local
  server usually needs none.
- **Models**: the models the provider lists at `GET <base URL>/models`, sent
  with the key as a bearer token; the ids are read from OpenAI's `data` list,
  a `models` list or a bare list. They are detected when the settings or the
  sidebar open, again with **Detect**, and after the service, base URL or key
  changes; a provider that lists none says why.
- **Connection**: **Test** detects the models again and sends the model in
  use there, or the first one listed, one short request of the kind the
  agent sends: a forced tool call with the model's reasoning off. It says
  whether the model answered, in how many seconds, and whether it made the
  tool call the agent's jobs need, so a wrong key, a busy service or a model
  that rejects `reasoning_effort` or tools shows here first.

The model the agent uses is chosen at the top of the lookup sidebar, from a
list of every provider's detected models grouped under the provider's name;
the button beside it detects them all again. The choice is stored with the
vault's settings. Until a model is chosen, or while its provider is gone, the
agent is off and lookups show only the deterministic results.

Under **Web search**:

- **Exa API key**: optional. Empty, web searches use Exa's keyless, free and
  rate-limited service; a key selected or added in SecretStorage is sent in
  the `x-api-key` header and moves searches to that Exa plan's limits.

Protocols: [Exa MCP](https://exa.ai/docs/reference/exa-mcp),
[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
and [DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/).

The model sees the selection or the question, the paragraph around a
selection (for Find sources also the source's opening lines and the references
it cites) and the records and web results the agent reads; web searches send
their queries to Exa. Capture's drafts and `kgd` runs
stay on this machine.

## Not yet built

Later owner-scheduled frontend work: editing commands, jump links from md/tex
sources to records, a shipped `.base` file and a Bases view. For the agent:
the in-place overwrite of superseded records and relocation, linking pending
uses after a capture, and compiling a whole source.

## Installation

Open the base root as a vault in Obsidian once, so that its `.obsidian`
directory exists, then run:

```bash
kgd obsidian install [--base B]
```

The command works on a registered base: the one named by `--base`, or by
default the base whose root contains the working directory. It refuses any
other root, and a base whose `.knowledge` is a symlink; register a root first
with `kgd base add`. It:

- copies the bundled `main.js`, `manifest.json` and `styles.css` to
  `<root>/.obsidian/plugins/kgdistiller/`, replacing an older bundle;
- adds the plugin to the vault's `community-plugins.json`;
- sets `hiddenKnowledgeEnabled` to `true` in the plugin's `data.json`, keeping
  every other key, so Obsidian indexes `.knowledge/` without a manual step;
- warns when `.obsidian/app.json` sets **New link format** (`newLinkFormat`) to
  `relative`. That format writes `../` links, which are outside the record
  grammar; keep it unset (shortest) or `absolute`.

It prints one JSON object: `status` (`installed`, `updated` or `current`),
`plugin_id`, `plugin_version`, `base`, `vault`, `plugin_root`, `files`
(`{path, bytes}` per copied file), `hidden_indexing` (`enabled` or `current`),
`community_plugins` (`updated` or `current`), `warnings` (strings) and
`reload_required` (true when anything was written).

Reload Obsidian afterwards. The plugin is desktop only.

## Hidden-folder indexing

### Settings and operation

In the plugin settings, under **Hidden knowledge folder**:

- **Index hidden knowledge folder** switches indexing on or off. It is stored
  as `hiddenKnowledgeEnabled`, default `false`; `kgd obsidian install` sets it
  to `true`.
- **Indexing status** reports the result and offers **Rescan**.

Indexing covers the whole `.knowledge` subtree and nothing else; other dot
folders, including Obsidian's configuration directory, remain outside the
feature's scope. The folder is the product's single knowledge root, so it is
not configurable, and nothing under it is excluded.

Stored plugin settings are type-checked at load. A value whose type differs
from the default is replaced by the default and named in a notice; keys the
plugin does not have, including those of removed settings, are ignored.

If `.knowledge` does not yet exist, the setting is retained and the status
reports it as missing. After creating it, select **Rescan** or run **Rescan
hidden knowledge folder** from the command palette
(`kgdistiller:rescan-hidden-knowledge-folder`). A symbolic link in place of
the folder is reported as invalid and not indexed. Changes within the indexed
folder are delivered through the adapter's normal file reconciliation and
watching behavior, which is what keeps the graph live.

### Compatibility and boundaries

Hidden-folder indexing requires the desktop filesystem adapter. A desktop
adapter without the required capabilities reports an unsupported status. The
feature uses private Obsidian reconciliation and watcher APIs because the
public API does not provide a hidden-folder allowlist. A later Obsidian release
can require adaptation; capability checks must fail with a bounded status
instead of claiming successful indexing.

Do not run this feature alongside **Hidden Folders Access**. If that plugin is
enabled or loaded, kgdistiller reports the conflict and leaves its own hidden
indexer inactive. Disable the other plugin, then rescan. Indexing exposes
`.knowledge/` to Obsidian search and to other plugins of the vault; it does not
make hidden files available to other vaults or devices, and it does not
promise compatibility with every third-party plugin.

Indexing is configured during plugin load before workspace restoration. Plugin
unload restores its adapter hooks and releases its watches while retaining the
native cache entries and workspace leaves. This avoids removing an open hidden
note during plugin reload. Explicitly switching the setting off removes the
injected cache entries so the folder becomes hidden again; it does not delete
the files on disk.

### Verified behavior

On Obsidian 1.13.7 for macOS, hidden indexing passed native editor, search,
backlinks, internal file renaming with link updates, external nested directory
changes, Unicode names and case-only rename checks. Plugin reload and vault
reload preserved an open hidden note. These checks cover the tested desktop
version; they do not establish behavior on all future app versions.

The plugin's vitest suite verifies hidden indexing with a mocked adapter, the
lookup's shaping of kgd's answers (the neighbour graph and its kind colours,
the `requires` chain, inbound links, the open file's target and a source's
records), the case folding, and the agent's tools and checks.

Still to be checked by hand in Obsidian 1.14.4 on a real vault:

- a Properties edit of a role list round-trips and `kgd check` stays clean;
- Backlinks groups a record's incoming links by role name;
- the sidebar follows an opened record, source and sheet, and the neighbour
  graph and its legend render in the light and dark themes;
- **Open source** lands on the record's first line in the latex-live and
  tinymist views;
- hidden indexing is on after `kgd obsidian install` without a manual toggle.

## Source and distribution

The adapter strategy is adapted from
[Hidden Folders Access 2.1.1](https://github.com/dsebastien/obsidian-hidden-folders-access/tree/de3734d36997a98b81a6a6644984748af1e6b3b0),
commit `de3734d36997a98b81a6a6644984748af1e6b3b0`. kgdistiller narrows the feature
to its own `.knowledge` folder and integrates it with its own settings and
lifecycle. The bundle also embeds Cytoscape.js and the fcose layout with its
dependencies cose-base and layout-base, all MIT. Every upstream license is
preserved in full in `THIRD_PARTY_NOTICES.md` at the product repository root
and in the distributed JavaScript bundle. The root build verifies that the
complete notices, including the Hidden Folders Access revision and the
Cytoscape, cytoscape-fcose, cose-base and layout-base copyright lines, remain
in the bundle.
