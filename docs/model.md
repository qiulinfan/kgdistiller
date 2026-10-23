# Knowledge model

kgdistiller stores research knowledge as plain Markdown records inside the
Obsidian vaults that hold their sources, and serves retrieval from one derived
SQLite file. This document is the normative description of the model, the
files and the write path. Retrieval is in [retrieval.md](retrieval.md), the
Obsidian plugin in [obsidian.md](obsidian.md), installation and restore in
[deployment.md](deployment.md).

## Principles

1. **Two authorities, everything else derived.** The authorities are the base
   files (`<root>/.knowledge/entries/*.md` for accepted knowledge and
   `<root>/.knowledge/drafts/*.md` for proposals) and the home files
   (`$KGDISTILLER_HOME/config.json` and `$KGDISTILLER_HOME/types/*.md`). The
   database is derived.
2. **One file format for every knowledge object.** Nodes and n-ary relations
   share it; which one a record is follows its kind's section in its base's
   document types. A theorem, a finding or an example is a node whose roles
   link what it is about. Every role value and every `requires` value is a
   link to a record.
3. **No hashes, no content-derived ids, no compatibility code.** Change
   detection uses file stat; evidence freshness is checked by finding the quote
   text in the source; re-embedding is decided by comparing stored text;
   concurrency uses one lock file, create-only moves and edit tools that refuse
   stale reads.
4. **Sources by format.** A local source is any `.md`, `.tex` or `.txt` file
   of a base outside hidden directories, read as UTF-8 text and addressed by
   path, 1-based inclusive line ranges and verbatim quotes; a foreign source
   is a web address, addressed by verbatim quotes of its text. The product
   reads no source syntax but its knowledge markers, which name the records a
   source states, and never downloads a source.
5. **No owner vocabulary in product code.** The product fixes only the
   record's fixed keys, the understanding values, the section names
   `Search terms` and `Evidence`, the folder names `entries` and `drafts`, the
   link grammar and the marker syntax. Node kinds, relation kinds, roles and
   epistemic labels are user data. Optional starting profiles live in
   `templates/document-types/`; they become active only when the owner copies
   and registers them in the home. The product activates no types and ships
   no default embedding model.
6. **Files decide validity; the database serves retrieval.** Validation and
   writes read files only. Retrieval reads the database only, except
   `get --source-lines`, which reads live source text on request.
7. **The database may lag, never differ.** It is a pure function of
   `config.json`, the `entries/` files of the registered, available bases and
   the embedding model. An incremental index produces the same rows as a
   rebuild, and losing the database costs one command plus the time to
   re-embed.
8. **A record asserts only what its cited text states.** A relation that
   cites a research source or the owner's notes quotes text stating that
   relation. A connection no source states, assembled from separate passages
   or across sources, is never written as a relation; capture and compile
   report it.
9. **Identity is permanent.** An accepted id and a base name are citation
   keys: they are never renamed for convenience and never reused for another
   concept.
10. **Every admission is a draft the owner accepts.** Agents only draft, new
    records and replacements of accepted ones alike; knowledge enters the
    base only when the owner accepts the draft
    ([Ingestion](#ingestion-drafts-and-the-owners-acceptance)).
11. **No entity without necessity.** In the owner's words: "我们的知识库的一条原则就是如无必要, 勿增实体. 所以当你觉得一个东西没必要的时候, 不再需要的时候, 请你直接删除, 不要做 fallback."
    Whatever a decision makes unnecessary, whether a kind, a field, a file, a
    code path or a document passage, is deleted outright in the same change,
    with no fallback and no compatibility path.

## Research knowledge selection

The graph is the knowledge the reader selects from a source already judged
worth studying: concepts, precise results and relations, rather than every
paragraph of exposition or motivation. Capture and compilation follow the
owner-defined document type that fits the source. They preserve the
conditions, formulas, limitations, counterexamples and experimental evidence
that affect meaning;
section names and source syntax never determine admission.

The knowledge is one graph. In the owner's words (2026-10-16):

> 这些东西其实都是节点. 而关系是多个节点之间共同构成的关系. 一个节点自己的 proposition, 不应该是一个关系.

Nodes are the concepts, methods, architectures, objectives and quantities a
source defines, and the results it states: a theorem (any precisely stated
result, whatever the source calls it: theorem, proposition, lemma or
corollary), a finding, an example. A statement node's roles link every record
it concerns, such as `theorem: [about]`, and a role may hold several values:
"the sum of two subspaces is a subspace" is a theorem whose `about` links
subspace and sum of subspaces, not an implication. A node kind that declares
roles links at least one record in them. A relation is a connection the
source states between records, of a kind the type declares: an implication
between results, a comparison, a generalization. It binds at
least two values (a participant may repeat), and it is itself a record, a
vertex of the graph that other records may link. How important a record is is
not its kind but its connectivity: retrieval reports its `degree`, the number
of distinct other existing records it links or is linked by. A document
type's guidance may set a review threshold for the number of nodes per
source.

Each concept has exactly one record, owned by the source that defines or
originates it. A source that only uses a concept, even one it explains at
length, never gets a node for it: the use links the concept's record. When no
base records the concept yet, it is captured on its own, from where it is
defined: a local source of the owner's, or the outside work that originates or
explains it, as foreign knowledge
([Foreign knowledge](#foreign-knowledge-and-localization)). A single capture
that cannot link it leaves it out of the values and reports it; a statement
node still links at least one record.

A label names one concept across every base. In the owner's words
(2026-10-15):

> 两个概念如果名字一样, 意义也近似 -> 那么就是完全相同的概念. 我们在入库的时候先查库, 不会出现这种状况; 那么同名不同意义呢? 很简单, 检测到之后一律在新概念的名字后加上 ({源文件名})

So no two records, and no two drafts, have labels with the same name key, and
a draft's label is no accepted record's except the one it replaces. The lookup
before every draft finds a record of the same name; when it means the same,
the knowledge point is recorded already, and when it means something else,
the new concept's label takes its source's name: `Label (source)`, where the
source is the file's name without its extension, or a web page's last path
segment, decoded, with underscores read as spaces (`Autoencoder
(Autoencoder&VAE)` for a note `Autoencoder&VAE.md`). `kgd check` and `kgd accept` refuse a repeated label with
that suggestion. Aliases are not unique.

Accepted records, including relations, are the units of lexical and dense
retrieval. Their links and evidence locate the original passages. Local source
text remains available for on-demand context through the record's base,
relative `source` path and `lines`; `get --source-lines` reads that context
live. No second paragraph store or source-format parser is needed for this
connection.

## Ingestion: drafts and the owner's acceptance

In the owner's words (2026-10-15):

> 你想: 我什么时候会想要入库一个知识呢? 首先, 他一定是被 agent 提取出来作为一个知识了, 这是毋庸置疑的. 因而就有这么几个情况:
>
> * 第一个情况, 我 command 一个词语 (也就是问了他 "这是什么"). 这个时候我大概率就是根本不知道他是什么意思的. 我看完词条 -> 理解了, 把它从 draft 里稍微修订一下, 然后入库, perfect.
> * 第二个情况, 我对一整个文档都很熟悉, 用一个 skill 去扫它, 获得一堆节点. 这个时候, 我在 draft 里扫一眼, 一个一个 accept
>
> 你发现区别了吗?
> 没有区别!!!!!
> 所以, 只要一个知识入库, 他就是直接进入 draft, 然后等待我审阅和修订. 不过, 它所有涉及的所有直接边关系, relation 关系都要我一一审核, 并且可以修订.

So every way knowledge enters the base ends in drafts: a capture of one item
while reading, a compile of part or all of a source, and a re-ingested source.
Agents only write drafts; moving where a record cites, a localization
included, changes no knowledge and is `kgd relocate`
([Finding and relocating passages](#finding-and-relocating-passages)). A
draft is either a new record or, under the id of an accepted record of the
same base, that record's replacement. The owner reads, revises and accepts each draft, its links
included, and `kgd accept` admits it at once; deleting a draft rejects it.
There is no other review surface and no generated sheet.

## Foreign knowledge and localization

Knowledge is learned top down: a reader assumes the lower-level results a text
builds on and learns them later, if at all. Such a concept still gets a record
when the owner captures it, even when its defining text is not among their
files. In the owner's words (2026-10-15):

> 其实这个 pending 不应该叫 pending, 而应该叫 foreign: 非本地的知识. 就是说, 这个知识的出处其实是外界的文章. 并且你说的是对的. 它仍然一样: 被扫描, capture 后放入 draft. 但是呢, 它是一个 foreign 的内容.
>
> * 这个 foreign 的内容, 难道一定就要在我本地把那个文章, 或者 wikipedia 页面下载下来, 才算通过吗? foreign 其实只是说明 "这个知识不在本地文件里", 但是对理解照样没有影响. 我照样可以把它从 draft accept 到正式的知识库. 它只是一个 bool 标签而已, 并且它的证据不是本地而是外界超链接.
> * 并且注意: foreign 知识是可以 localize 的 (虽然不一定必要). 比如我查找 autoencoder 这个概念的出处, 然后我去把这个文章给 down 下来了 -> 我在这个文章里找到这个词并快捷键搜索 -> 搜到我的 autoencoder 这个词. 这个时候, 不管它是 draft 还是已经在正式知识库中, agent 都做一个 "localize": agent 把 source 从外界给迁移回本地.

- **A foreign record** is a record whose `source` is a web address (`http://`
  or `https://`): the work that originates the concept, or a page that
  explains it, such as an encyclopedia article. It has no `lines`; its
  Evidence quotes are copied verbatim from that page when it is drafted.
  Foreign is not a stored field: it is whether `source` is a web address. A
  foreign record is ordinary knowledge in every other way: it is accepted,
  linked, retrieved and marked understood or not like any local record.
- **Its base** is the base of the source the owner was reading when they
  captured it, and its `type` is one of that base's document types, the one
  that fits that source. It takes no knowledge marker: its evidence lies on
  the page, not in a file of the owner's.
- **Localization** moves a record's evidence from the outside work to a local
  source once the owner holds that work, for example after translating the
  paper into their vault. Looking the concept up while reading the local
  source offers it; the agent checks that the local passage states the same
  knowledge point and moves the record there with `kgd relocate`, into that
  source's base when it is another
  ([Finding and relocating passages](#finding-and-relocating-passages)).
  Localization is never required: a foreign record is complete knowledge.

## Knowledge markers

A record knows where its source states it through a knowledge marker: the
record's name written into the source, at that place, in the source's own
syntax. In the owner's words (2026-10-23):

> 2. 这样之后, 我们标记就好找了. 如果我们想溯源一个东西, 我们就在 obsidian 的这个 vault 中所有的被支持的格式文件, 在全文用标记符号. 这样应该比全局 grep 要快一个数量级. 
> 注意: 我们的知识是全局的, 并且一条知识应当知道它的 base vault 是什么. 因而可以跨 vault 找.

A marker is found by reading the sources of every base, so a record of one
base may be marked, and found, in a file of another. Reading the sources for
markers is a scan of their text, about 0.6 s for the 64 files and 299 markers
of one base; what markers add over a text search is an exact, stable name for
each place, not an order-of-magnitude speedup.

| Format | Marker |
|---|---|
| md, txt | `--[[NAME]]--`, or `--[[NAME\|TEXT]]--`, which shows TEXT |
| tex | `\kn{NAME}`, or `\kn[NAME]{TEXT}`, which shows TEXT (it may be empty); a marker after an unescaped `%` is a comment |

- A marker lies on one line, so writing one never changes a source's line
  count and moves no other record's `lines`.
- NAME resolves by name key to the record whose label, label without its
  `{scope}·` prefix, alias or id it is, among the accepted records and the
  drafts of every base. A label written `{scope}·{name}`, as the owner's
  document types name statement nodes (theorems, findings, examples), is
  also named by its `{name}` alone. When several records answer to NAME, the
  one citing the marker's own file wins, then the only one of the file's base;
  otherwise the marker names no record and lists its `candidates`.
- Markers live only in the sources: nothing stores them, and the index does
  not read them.
- A foreign record takes no marker: its evidence lies on a web page.

Who writes them, and which document type a record follows, in the owner's
words (2026-10-23):

> 1. 文档类型怎么定:  agent capture draft 时根据文档自动标注吧. 这个语义理解能力肯定还是有的. 你觉得呢? 
> 2. 谁写标记: 也是 agent capture draft 时标记. 反正我不满意可以去掉, 没关系的 
>
> 3. 生成的文件会冲掉标记: 只要翻译 skill 改成兼容 kgdistiller 就行了

And on how a marker sits in the text:

> 对于描述性的知识, 接入为节点的时候在原文最好后面加一个冒号而不是直接蹦出来
> (--[[Transformer·Learned vs sinusoidal positional encoding]]--): ....

So the agent that drafts a record chooses its document type from what the
document is and writes its marker:

- The capture and compile Skills and the plugin's Capture run
  `kgd mark DRAFT` after writing a local draft. It writes the marker on the
  line of the draft's first Evidence quote: `--[[LABEL]]--:` in md and txt, a
  colon introducing the passage it names and a space before the text, and
  `\kn[LABEL]{}`, which prints nothing, in tex. When the source already writes
  the item's name where it defines it, the agent wraps that name in the
  source's own marker syntax instead.
- The owner removes a marker they do not want, by hand; a record without a
  marker is complete knowledge.
- A file regenerated by another tool, such as a translation, keeps its
  markers only when that tool writes them back; making the owner's
  translation Skill write them is that Skill's concern, not kgdistiller's.

`kgd markers` lists markers with the records they name, `kgd source` lists
those of one source, and the Obsidian plugin draws them and opens their
records ([obsidian.md](obsidian.md#knowledge-markers)).

## Finding and relocating passages

Sources change: text is inserted, passages are reworded, files are renamed,
moved, merged and split. A record follows its knowledge through its markers.
In the owner's words (2026-10-23):

> 3. relocation skill 这样就好做了: 首先, 对于非 foreign 的知识, 在它被声明所在的 vault 的所在的文件找标记, 如果找到就 ok, 找不到就记录缺失; 并行地对于所有确实的知识, 在它的 vault(vault 一定 canonical, 不需要考虑它被转移到其他 vault 的情况....吗? 实在找不到可以第三轮考虑) 通过 recursively 找支持格式文件的标记点 的方式来重定位它. 注意既然是 agentic 的, 那么也可以处理它变成了近似语义等等的情况. 顺便如上下文更新了, 也可以更新.  

> 4. 找不到就改成 foreign: 我觉得, 知识库主要是 agent 看的, 次要才是我看的. 如果我发现一个知识不在原位了, 我就主动发起一次查询 foreign 就能解决. 并且我认为本身知识易位就是少见的事情, 知识移位后找不到更是少见(因为同一个文档如果改名了或者换位置了, 他们肯定是一起的. 我们找到一个就能找到其他, 找不到一个其他也找不到, 这很明显的), 所以这种情况才是极少数事件. 我觉得你不应该过度防御. 

The rules that follow:

- **Markers are followed in three rounds.** For a local record whose quotes
  are gone from its cited file, or whose file no longer exists, the record's
  markers are searched first in its own source file, then in every other
  source of its base, then in every source of the other bases; the first
  round that finds any stops the search. When its markers lie in exactly one
  file whose text holds each of its quotes exactly once, that file and those
  lines are where it went. Otherwise the passage was reworded, and the
  markers say where to read.
- **`kgd check` reports what to follow** (`follow`,
  [Validation](#validation-kgd-check---base-b---fix-lines)), and
  `check --fix-lines` moves the records whose markers lead to another file of
  their own base.
- **`kgd relocate UID`** without a passage moves a record to where its
  markers lead, into another base with every link to it when it lies there,
  keeping its Evidence. Given a passage's text, it moves the record to the
  only passage of the sources that holds the text, which becomes its
  Evidence when the old quotes are not there. Given `--url` and a web page's
  own text, it makes the record foreign. Only where the record cites changes;
  it is the one write an agent makes to an accepted record without a draft,
  because it changes no meaning.
- **`kgd find`** (MCP `kg_find`) finds a text in the sources, read live: a
  passage the owner pastes, or every place a name occurs, such as where a
  concept is defined. It is read-only and deterministic, and loads no model.
- **The relocate-kgdistiller Skill** judges what the commands cannot. It runs
  `check --fix-lines` and `kgd relocate UID` for what only moved. Where the
  markers name a record but its quotes no longer hold, it reads the marked
  passage: the same knowledge reworded is relocated to the new passage with
  its text, and a changed meaning gets a replacement draft. A record that no
  marker names is looked for once with `kgd find`; when the passage is
  nowhere in the owner's files, the Skill finds the outside page that states
  the knowledge and makes the record foreign with `kgd relocate UID --url`
  and the page's own text. It also relocates to a passage the owner pastes,
  and finds the record from the knowledge's name, exact or approximate.

## Understanding

`understanding` is `yes` or `no` and defaults to `yes`: accepting knowledge
normally means the owner understood it. The owner marks `no` by hand on what
they accept without understanding it, and finds those records with the
`understanding` filter of every retrieval command.

## Conflicts, updates and relocation

The owner's statement below governs how conflicts between new and existing
knowledge are detected and resolved, how accepted knowledge is updated and how
it is relocated (rebased) to another source; the rules for them in this
document follow it. "1a" is the rule that each concept has exactly one record,
owned by the source that defines it (see
[Research knowledge selection](#research-knowledge-selection)). Its mentions of
pending predate the 2026-10-15 statements above, which replaced pending terms
with links to foreign or local records and made every admission a draft.

> 你知道, 我们的知识库是一个 agentic 的知识库.
>
> * 入库与索引都是 agentic 的.
>
> 其实这两步, 都应该做冲突处理.
> 什么流程呢?
>
> 1. 入库一条知识前(不论是单条, 多条还是全文都一样. 入库一篇文章/多条知识就是多次入库单条), 我们首先要索引这个知识. 这种是为了两个目的:
>    1. 一个是刚才的 1a: 我们要确认不存在重复的知识词条定义. 注意: 这里的检索甚至包括 pending. 因为入库一条知识如果和一条 pending 撞了, 那正好就同时把 pending 划掉了. 这样就考虑到了所有的情况; 这个行为保证了 1a 的成立性.
>    2. 另外一个就是冲突处理. 也就是说, 当我改写一篇笔记时, 我会自己重新入库它. 这个时候, 所有的知识在入库前都会索引一次. 当发现同一知识点已经定义过的时候:
>       1. 如果意义没更改, 我们就直接忽略它.
>       2. 如果意义更改了, 我们就直接覆写它.
>       3. 这里顺便说一下, 我们诸如笔记这类文件也会经常改名, 甚至合并和拆分. 因而我们并不能保证同一知识点覆写/重复入库的时候是在同一篇文章的. 还有的情况包括知识重定位: 比如假设 sigma algebra 本来在 probability 里定义了一个狭义的意思, 后来在 measure theory 中定义了一个更广义的意思 -> 检测到覆写, 这个知识词条重新写, 并且它的 source 也从 prob theory 改成 measure theory.
>
> 另外, 当我们把一条知识记录为 pending 之前, 我们也要进行一次检索, 查看是否已经有过定义. 如有, 不要把它设置为 pending, 它是一个已知的知识. 假设此处是一个定义, 那么这里定义出的新知识就自然地和刚查出来非 pending 的这个知识形成一个 relation.

The owner's answers to three follow-up questions (which of two inconsistent
definitions overwrites the other, what happens to knowledge that disappears
from its source, and whether relocation crosses bases), also verbatim:

> 关于这三个问题:
>
> 1. 两处定义不一致时, 谁覆写谁?
>    1. 新定义 tend  to 覆盖旧定义, 但是当且仅当它更 general 或者纠正了旧定义, 才直接覆盖; 其他情况下, 入库 agent 让用户进行裁决.
> 2. 某个知识点不见了? 那么它将变为 pending, 就像是一个 git commit 一样. 他最终的命运是充新建立到其他知识源还是被遗弃? 将它变成 pending 的 agent 不用管, 而是我来管.
> 3. 重定位当然可以跨 base, 因为检索是全局的. 那么重定位当然是全局的.

The rules that follow from these statements:

- **Look up before every draft.** Admitting a source, or several items, is
  admitting each knowledge point in turn. Before drafting one, the agent looks
  it up in every registered base with `kgd resolve` and `kgd search`, and in
  the drafts with `kgd drafts --name`. Every concept a value would name is
  looked up the same way: a recorded or drafted concept is linked, and in a
  definition that link is the relation between the new knowledge and the
  known one.
- **A new knowledge point** gets a new draft. A concept that shares the name
  of a recorded one but means something else is new, and its label takes its
  source's name (1a, "Research knowledge selection").
- **The same knowledge point with the same meaning** gets no draft. If its
  recorded citation no longer verifies because the source was renamed,
  merged, split or reworded, the agent relocates it to the passage being
  admitted with `kgd relocate`.
- **The same knowledge point with a changed meaning** gets a replacement draft
  rewritten from the new passage, keeping the id and the owner's
  `understanding`, with `source` and `lines` moved there. A replacement lives
  in its record's base, so when the passage lies in another base the agent
  drafts nothing and reports the proposed replacement; moving the record there
  first, with `kgd relocate`, is the owner's call. The
  agent reports whether the new definition is more general than the recorded
  one, corrects it, or conflicts with it in any other way, a narrower one
  included; accepting the replacement is the owner's decision in every case.
  A definition that the recorded one explicitly rules out, such as the error
  it corrected, is reported as a conflict, never as more general.
- **Relocation is global.** `kgd relocate` finds the passage, or the
  record's markers, in every base. When it lies in another base, the record moves there with
  its id and every link to it, in every base, is rewritten to the new uid; it
  refuses when that base already has a record or draft with the id.
- **Knowledge that disappears is the owner's.** A record whose knowledge a
  re-admitted source no longer contains, or whose source file no longer
  exists, is reported, and `kgd check` reports it stale or its source missing,
  with where its markers lead. The relocate Skill follows them, and makes a
  record that no marker names and no source states foreign, citing the
  outside page that states it
  ([Finding and relocating passages](#finding-and-relocating-passages)).
  The agent deletes nothing: the owner decides whether a record is
  re-established from another source through a replacement draft, or deleted,
  together with the links to it.
- **Who writes what.** Agents write drafts only. The owner accepts them with
  `kgd accept` (the Obsidian Capture view runs it) and edits accepted records
  by hand. The only agent edits of accepted records are `kgd relocate`, which
  moves where a record cites without changing what it says and, across bases,
  rewrites the links to it, and the link rewrites that a merge or a split the
  owner accepted requires.

## Data model

| Concept | Representation |
|---|---|
| Home | `$KGDISTILLER_HOME` (default `~/.knowledge`): base registry with each base's document types, the document types, embedding model id, the database and the lock. |
| Base | A registered directory, normally one Obsidian vault, holding its own knowledge under `.knowledge/`. Its name is a slug that exists only in `config.json`. |
| Source | Local: an `.md`, `.tex` or `.txt` file of a base outside hidden directories, read as UTF-8 text with 1-based lines. Foreign: a web address, the outside work or page whose text a foreign record quotes. |
| Document type | `types/<name>.md`: `node_kinds` and `relation_kinds`, each a mapping of kinds to their ordered roles (a node kind's may be empty), and optional `epistemic`; the body is guidance. |
| Record | One file `.knowledge/entries/<id>.md`. Its global address is the uid `<base>:<id>`. It is local or foreign by its `source`, and follows the document type its `type` names, or its base's only type. |
| Marker | `--[[NAME]]--` in md and txt, `\kn{NAME}` in tex: a record's name written into a source where the source states it. Found by reading the sources; never stored. |
| Node | A record whose kind is a node kind: a concept, or a result a source states, such as a theorem, a finding or an example, whose roles link every record it concerns (at least one when its kind declares roles). |
| Relation | A record whose kind is a relation kind: a connection between records, binding at least two values to that kind's roles. Arity is unbounded beyond that; a binary relation binds exactly two values, in two roles or in one symmetric role. A participant may repeat (self-relation) and may be a relation (a relation about relations). |
| Application / example | A node of a kind the owner registers for it, such as `example: [uses, setting]`. |
| Link | A value `"[[id]]"`, `"[[base:id]]"` or `"[[.knowledge/entries/id]]"` in a role list or in `requires`; every value is a link. A role value is a knowledge reference, so a literal setting, hyperparameter or measured value belongs in the prose and evidence, never in a role, and a concept without a record is captured before it is linked. |
| `requires` | A list on every record holding links to its direct understanding prerequisites, the one form of a prerequisite. A proof or derivation dependency the source states is a relation between results, such as `implies` with the results a proof uses as premises. |
| Evidence | `source` + `lines` (one or more ranges) + one or more verbatim quotes in the body's final `## Evidence` section; a foreign record has quotes of its web page and no `lines`. The quotes are the anchor; each lies inside one cited range of a local source. |
| Understanding | `yes` or `no`; absent means `yes`. Independent of coverage and evidence. |
| Epistemic | Optional; a value from the type's `epistemic` list stating how the cited passage itself supports the record, such as proved or measured there, or only stated. A result the source reports from other work is stated. It is never a verdict of the whole knowledge base. |
| Draft | `.knowledge/drafts/<id>.md`: a proposed record in exactly the record format, new, or the replacement of the accepted record with its id. Not knowledge and not indexed. |

There are no source ids, courses or groups, passages, stored sense groups,
pending terms or lists, sheets, edge files, identity or alignment registries,
review or curation status, receipts or plan files, per-record `id`, `schema`
or `foreign` fields.

## Home and base layout

```text
$KGDISTILLER_HOME/                 default ~/.knowledge; absolute after ~ expansion
├── config.json                    bases, their document types, embedding model id
├── types/<name>.md                one document type per file
├── .gitignore                     "index.sqlite*" and "lock", written when the home is created
├── index.sqlite (-wal, -shm)      derived database
└── lock                           write lock; its contents are meaningless

<base root>/.knowledge/
├── entries/<id>.md                accepted records: nodes and relations, one folder
└── drafts/<id>.md                 proposed records, new or replacements, same format
```

`KGDISTILLER_HOME` is the only environment variable the product reads for its
own data. Under `.knowledge/` the product reads and writes only `entries/`
and `drafts/`; other files there are ignored. The product writes no
vault `.gitignore`.

`config.json` has exactly the keys `bases` and `embedding`. Each base has
exactly `path` and `types`; base names match `^[a-z0-9][a-z0-9-]*$`. A path
under the user's home is stored as `~/…`. No base root may contain another,
and the home may not lie inside a base root. `types` lists the document types
of `types/` that the base's records may use, each once. `embedding` is
`null` or a Hugging Face id of an XLM-RoBERTa sentence encoder that ships `sentencepiece.bpe.model`, such as `BAAI/bge-m3`, with no revision pin; with `null`
the index is lexical and name only. After a change, the next index run of any
kind drops the old vectors, and a plain `kgd index` embeds every record with
the new model. `kgd embedding set MODEL` and `kgd embedding off` change it
after checking the model ([retrieval.md](retrieval.md#the-embedding-process)).

A type file's stem matches `^[a-z0-9][a-z0-9-]*$`. Its frontmatter, read with
PyYAML `BaseLoader`, has only `node_kinds` (required, a non-empty mapping of
kind slugs to lists of unique role slugs matching `^[a-z][a-z0-9-]*$`, never a
fixed key; a node kind's list may be empty), `relation_kinds` (the same
mapping, each list non-empty) and `epistemic` (unique slugs; without it
records of this type may not carry `epistemic`). No kind is both a node kind
and a relation kind, in one type or across the types of one base. The body,
the guidance, is non-empty.

### Sources

A base's sources are found by their format, never registered one by one. In
the owner's words (2026-10-23):

> 1. 
> 我其实希望支持文件类型: 意思就是, 一个 vault 下递归的所有的文件, 只要是这个格式(比如现在开始支持 md, tex 和 txt) -> 那就包括, 而不要有固定的文件形式, 这非常不灵活, 而且其实用处有限, 不是必需品. 我们不需要一个得频繁手操的设计来服务于一个不必要的用途. 
> 不过与之对应的: 我们得修订每个格式的文件在 obsidian 中的标记的样式和编辑方法-> 比如说 tex 中如果有一个东西标注了 \kn{}, 那么就要给它设置cmd+点击, 跳转到知识源词条的链接; 知识词条一定是 md, 跳转回来统一快捷键; 并且不要在 
> 并且注意2: 在 obsidian 中, 对于这些文件格式, 这些标记的词条全都可视化, 比如 \kn{} 在光标不在他周围的时候保持一个紫色的包裹而不是 \kn{}文字. 
> 并且注意2: 和我的 obsidian-latex-live 兼容.
> 并且注意3: 一个标记为 foreign 的知识是不需要标记的. 以及, 没有标记的知识如果重定位不到, 那么就改成 foreign. 这是我们下面修订 relocation skill 的一个准则

So every file with the suffix `.md`, `.tex` or `.txt`, in any case, under a
base root is a source of that base, except hidden files, anything under a
hidden directory (`.knowledge/`, `.obsidian/`, `.git/`, `.trash/`) and a file
that resolves outside the root through a symlink. Any other file is not a
source, and `kgd source` and `check` say so. A source has no document type of
its own: each record declares the type it follows in `type:`, one of its
base's `types`, and may leave it out when the base has exactly one, which then
applies. The agent drafting a record chooses the type that fits the document
([Knowledge markers](#knowledge-markers)). The Obsidian side of the statement,
markers drawn as chips, Cmd/Ctrl+click and one jump command, is in
[obsidian.md](obsidian.md#knowledge-markers); unmarked knowledge that cannot
be relocated becomes foreign
([Finding and relocating passages](#finding-and-relocating-passages)).

## Record format

A record is YAML frontmatter followed by a Markdown body. The file starts with
a line `---`; the frontmatter ends at the next line that is exactly `---`.
Files are read as UTF-8 with a leading byte-order mark dropped and universal
newlines.

```markdown
---
label: Sum of two subspaces is a subspace
kind: theorem
type: math-notes
about:
  - "[[subspace]]"
  - "[[sum-of-subspaces]]"
epistemic: stated
source: notes/math/linear-algebra/chapters/01-review.tex
lines: 24-32
---
若 $U_1,U_2$ 是 $V$ 的 subspaces，则 $U_1+U_2$ 也是 subspace。

## Conditions

$U_1,U_2\subset V$ 均为 subspace。

## Evidence

> 两个 subspace \(U_{1},U_{2}\) 的 sum \(U_{1} + U_{2}\) 也是一个 subspace, 并且

> 且 \(U_{1} + U_{2}\) 是同时包含 \(U_{1}\) 和 \(U_{2}\) 的 \(V\) 的最小 subspace.
```

A foreign record cites a web address and quotes its page:

```markdown
---
label: Autoencoder
kind: definition
source: https://en.wikipedia.org/wiki/Autoencoder
understanding: no
---
An autoencoder is a neural network trained to reproduce its input through a
learned code of lower dimension.

## Evidence

> An autoencoder is a type of artificial neural network used to learn efficient codings of unlabeled data
```

**Fixed keys**

| Key | Required | Value |
|---|---|---|
| `label` | yes | Non-empty single-line string in any language, unique by name key across every base (see [Research knowledge selection](#research-knowledge-selection)). |
| `kind` | yes | A node kind or relation kind of the record's document type. |
| `type` | when the base has several types | The record's document type, one of its base's `types`; absent means the base's only type. |
| `source` | yes | A POSIX path relative to the base root, no leading `/`, no `..`, naming a source of this base (an `.md`, `.tex` or `.txt` file outside hidden directories); or a web address `http://…` or `https://…`, which makes the record foreign. |
| `lines` | local only | One or more ranges `a` or `a-b`, comma-separated, ascending and disjoint, each with 1 ≤ a ≤ b ≤ the source's line count. A foreign record has none. |
| `aliases` | no | List of single-line names in any language, symbols. Not unique. |
| `understanding` | no | `yes` or `no`; empty or absent means `yes`. |
| `epistemic` | no | A slug from the type's `epistemic` list: how the cited passage itself supports the record. |
| `requires` | no | List of values. |
| `tags`, `cssclasses` | no | Obsidian's own properties; accepted and ignored. |

**Role keys.** Every other key is a role declared for `kind`, holding a list of
values in order. Roles are top-level list properties so that Obsidian's
Properties panel edits them with link autocomplete and Backlinks groups
incoming links by role. A kind whose meaning does not change when its
participants swap declares a single role and lists them unordered, as in
`equivalent: [side]`; a kind with several roles is directional, each role with
its own meaning, so one fact has one form.

**Class rule.** A record is a relation if and only if a document type of its
base declares its kind under `relation_kinds`; otherwise it is a node. Its
roles are those its kind declares. A relation binds at least two values across
its roles, a participant possibly repeated; a node kind that declares roles
links at least one record in them. A result the source states is a node whose
roles link what it is about, as in the example above; an implication between
two results, such as `implies` with `premise: ["[[direct-sum-criterion]]"]`
and `conclusion: ["[[sum-of-subspaces-is-a-subspace]]"]`, is a relation. The
class is read from the types and never stored, so a type edit reclassifies its
records.

**YAML reading.** Frontmatter is read with `yaml.load(text,
Loader=yaml.BaseLoader)`, so every scalar is a string (`on`, `yes` and `40-46`
stay strings). It must be a mapping whose values are strings or lists of
strings; an empty scalar on a list or role key reads as an empty list. An
unquoted wikilink such as `premise: [[x]]` parses as a nested list and is
reported as "quote wikilinks". The product never re-serializes frontmatter; the
only product-side frontmatter edits are the textual `lines:` rewrite of
`check --fix-lines` (with `source:` for a record its markers followed),
`kgd edit`'s rewrite of one field of a draft, and
`kgd relocate`'s rewrite of `source:` and `lines:` and, when it moves a record
into another base, of the link values of that record and of every record and
draft linking it.

**Value grammar** (`requires` and every role value):

```text
value   := "[[" target ( "|" display )? "]]"        the whole trimmed string; display is ignored
target  := id | base ":" id | ".knowledge/entries/" id      a trailing ".md" is ignored
```

Targets match after NFKC normalization and casefolding. A string that is not
one whole link (plain text included), a target containing `#` or `^`, any other path
form, and a local link prefixed with the record's own base name are errors.
Wikilinks inside body prose are free navigation and are neither validated nor
indexed.

Every value is a knowledge reference, never a literal. Settings, hyperparameters,
resolutions, splits, units and measured values belong in the prose and
evidence. kgd records knowledge concepts, not measurements: comparing reported
values means locating the records and reading their evidence or the source
context (`get --source-lines`).

**Body grammar**

```text
<prose: any Markdown>
[## Search terms
<free text, one phrase per line>]
## Evidence
<one or more blockquotes separated by blank lines>
```

Level-2 headings are lines starting with `## ` outside fenced code blocks.
`## Evidence` is the last level-2 heading and occurs once; `## Search terms`,
when present, is the heading immediately before it. Other headings belong to
the prose. The Evidence section contains only `>` lines and blank lines; each
maximal run of `>` lines is one quote, with the `>` and one optional space
removed. At least one non-empty quote is required. Search terms hold
retrieval phrasings that are not names; names go in `aliases`.

**Gloss.** The first sentence of the first prose paragraph that is not a
heading: lines joined with spaces, ending at `。！？` or at `.!?` followed by
whitespace or the end, capped at 240 characters, computed at read time.

**Ids.** The id is the file stem. A valid id matches
`^[^\W_]+(?:-[^\W_]+)*$` (Unicode letters and digits, CJK included, joined by
single hyphens), equals its own NFKC casefold and has at most 80 characters.
`slug(label)` creates one: NFKC, casefold, replace each run of `[\W_]` with
`-`, strip `-`, and cut at the last `-` at or before position 80 (or at 80).
An empty slug means the writer chooses an id; collisions append `-2`, `-3`, … .
Ids are unique case-insensitively across `entries/` and `drafts/` of a base and
never derive from a hash.

An id comes from the record's meaning. It carries no section, figure, table,
equation or theorem number, source ordinal, path or kind, all of which change
when a source is revised or reorganized; a label may quote the source's own
numbering when that is the item's name, while the id still describes the
meaning. Once accepted, an id is permanent: it is never renamed for
convenience and never reused for another concept. A changed meaning of the
same knowledge point replaces its record through a draft with the same id (see
[Conflicts, updates and relocation](#conflicts-updates-and-relocation)).

## Drafts

- A draft whose id does not exist in `entries/` proposes a new record. A draft
  with the id of an accepted record of its base proposes that record's
  replacement: accepting it replaces the record's file, and the id stays.
- Updating a record is drawing it into a draft. In the owner's words
  (2026-10-15):

  > 我如果要更新一个词条, 那么我是不是得先 search 这个词条? 那么其实, all we need is: 一个把现有词条从上半边, 重新拖动进 draft 侧的功能.

  The record found by a lookup is copied, byte for byte, to `drafts/<id>.md`
  (in Obsidian, by dragging it from the lookup sidebar onto the Capture view,
  or **Revise as draft**); the owner edits that draft and accepts it, which
  replaces the record. Until edited, the draft equals its record and
  accepting it only removes it.
- A draft's links may target accepted records or other drafts, of any base.
  A link to an id that exists in `entries/` always means the accepted record.
  Accepted records never link to drafts.
- The owner may edit a draft before accepting it; the bytes present at
  acceptance are what is accepted. Deleting a draft rejects it, and nothing
  remembers the rejection: there is no rejection list, so a later compile may
  propose the same record again.
- Drafts are validated by `kgd check` and never indexed. `kgd drafts` lists
  them with what `kgd accept` would make of each.

## Identity and addressing

- The uid `<base>:<id>` is unique by construction.
- In a file, `[[id]]` is local and `[[base:id]]` is a cross-base link; its base
  must be registered and must not be the record's own base. Local links never
  change when a base moves. A base name is a permanent citation key written
  into every `[[base:id]]` that targets the base, so it is never renamed or
  reused; renaming it in the config would leave those links dangling.
- Obsidian resolves a link to the same-folder file first, and all records share
  `entries/`, so `[[y]]` in a record resolves to `entries/y.md` in Obsidian
  whenever kgd resolves it so. When a basename is not unique in the vault,
  Obsidian writes the path form `[[.knowledge/entries/y]]`, which the grammar
  accepts. kgd never falls back to aliases; a dangling link is reported with
  the records carrying that alias.
- A label names one record across every base, and a concept of another
  meaning with the same name adds its source's name; aliases are not unique.
  Each concept has one record, owned by the source that defines it.
- Accepted ids are permanent (see **Ids** above).
- A local record lives in the base whose source supplies its evidence; a
  foreign record lives in the base whose source the owner was reading when
  they captured it.
- The base for a path is the registered root containing the path's realpath;
  there is no upward walk.
- A CLI uid argument is `base:id`, or a bare `id` when exactly one base has it;
  otherwise the command lists the candidates and exits 1.

## Validation: `kgd check [--base B]... [--fix-lines]`

`check` reads files only and never opens the database. It covers every base by
default; `--base` (repeatable) narrows it. Its output is JSON:

```json
{"errors": [{"path": "/abs/root/.knowledge/entries/x.md", "rule": "link", "message": "premise[0]: dangling link [[y]]"}],
 "stale": ["/abs/root/.knowledge/entries/z.md"],
 "moved": [{"path": "/abs/root/.knowledge/entries/w.md", "lines": "41-47"}],
 "follow": [{"path": "/abs/root/.knowledge/entries/z.md", "round": "base", "base": "notes",
             "source": "notes/renamed.md", "lines": "12-14"}]}
```

Paths are absolute. The exit code is 1 when any list is non-empty. Each type
file and `config.json` report their first problem; a broken type file does
not hide a `config.json` error, and record rules run only once both are valid.

| Rule | Checks |
|---|---|
| `config` | `config.json` shape, base names, root nesting, the home outside every root, every root available, no symlinked `.knowledge`, `entries/` or `drafts/`, an unknown `--base`. |
| `type` | Each `types/*.md` obeys the type rules; every type a base names exists, and the types of one base declare a kind they share alike; a record's base registers a type, a record of a base of several types declares its `type`, and a declared `type` is one of its base's. |
| `id` | The stem is a valid id; ids are unique within `entries/` and within `drafts/`, case-insensitively. |
| `frontmatter` | The record file is a regular file, not a symlink; the frontmatter parses; required keys present with the right shapes (`lines` for a local record, none for a foreign one); every non-fixed key is a role of `kind`; `understanding` is `yes` or `no`. |
| `source` | A local `source` exists, is a source of this base (an `.md`, `.tex` or `.txt` file outside hidden directories) whose realpath stays inside the base root, reads as UTF-8, and `lines` lies within it. A foreign one is a web address and is never fetched. |
| `kind` | `kind` belongs to the record's document type; a node kind that declares roles links at least one record in them ("a theorem links what it is about in about; capture a concept no record holds first"); a relation binds at least two values ("a result the source states is a node whose roles link what it is about"); `epistemic` is in the type's list. |
| `link` | Every value is a link matching the grammar; every link resolves to an accepted record (of this base or another, read from files); no self-link; a cross-base link to an unregistered base names it. |
| `label` | No other accepted record, in any base, has a label with the same name key; for a draft, no other draft either, and no accepted record but the one it replaces. The message suggests `Label (source)`. |
| `body` | The body obeys the body grammar. |

Drafts follow the same rules with one change: links may also resolve to
drafts, of any base. An accepted record that links a draft is told to accept
it first.

**Evidence freshness** applies to local records; a foreign record is never
fresh, moved or stale. Quotes and source text are compared after
`" ".join(s.split())`.

- **fresh**: every quote occurs inside one cited range;
- **moved**: not fresh, but every quote occurs exactly once in the whole file;
  the report proposes the lines of each occurrence as ranges, adjacent or
  overlapping ones joined;
- **stale**: anything else.

Staleness never hides a record from the index or retrieval.

**Following markers.** For a stale record, and a local record whose source
file does not exist, `check` searches the record's markers in three rounds,
its own source file, every other source of its base, then every source of the
other bases ([Finding and relocating passages](#finding-and-relocating-passages)),
and lists it in `follow` when a round finds any:

- `{"path", "round", "base", "source", "lines"}` when the markers lie in
  exactly one file whose text holds each quote exactly once: the record went
  there, and `lines` are its quotes' lines in that file;
- `{"path", "round", "evidence": "absent", "markers": [...]}` otherwise: the
  markers found (as `kgd markers` reports them), whose passage no longer
  holds the quotes.

`round` is `source`, `base` or `bases`. A record no marker names is not
listed in `follow`. A followed record stays in `stale`, or among the `source`
errors, until it is moved.

**`--fix-lines`** runs under the home lock. For each moved file it re-reads the
file; if the text changed since the check read it, the file is skipped,
otherwise only the frontmatter's `lines:` line is rewritten (every other byte,
the byte-order mark and the newline style kept) through a temp file and an
atomic replace. A `follow` record with `lines` whose file lies in its own base
gets its `source:` and `lines:` lines rewritten the same way; one whose file
lies in another base is left to `kgd relocate`, which also rewrites the links
to it. The output adds two lists and keeps in `moved`, `follow` and `stale`
only the files not fixed:

```json
{"errors": [], "stale": [], "moved": [], "follow": [], "fixed": ["/abs/…/w.md"], "skipped": []}
```

## Writing knowledge

### Lock

`$KGDISTILLER_HOME/lock` is a dedicated lock file taken without waiting
(`fcntl.flock`, or `msvcrt.locking` on Windows). It is held by `accept`,
`relocate`, `mark`, `check --fix-lines`, `base add`, `base rm`, `embedding set`
and `embedding off`; a second writer fails
at once. No data file is ever locked or truncated. `kgd index` relies on
SQLite's own locking.

### `kgd drafts [--base B]... [--source PREFIX]... [--name NAME]...`

Lists the drafts of the available bases, read from files: it writes nothing,
takes no lock and opens no database. Each draft has the record fields of
`kgd get` except `degree`, `in` and `source_text`, with `out` as below, plus:

- `path`: the draft file;
- `replaces`: the uid of the accepted record this draft would replace, or
  `null` for a new record;
- `out`: every value as a link with the `state` of its target, in place of
  `kgd get`'s `exists`: `accepted` (with the record's `label` and
  `understanding`), `draft` (a draft of any base) or `missing`;
- `with`: the drafts, of any base, that it links, directly or through other
  drafts, which `kgd accept` must be given with it;
- `linked_by`: the drafts, of any base, that link it;
- `choices`: the `node_kinds`, `relation_kinds` and `epistemic` values its
  document type allows (for a draft that declares no `type` in a base of
  several, every type of its base, in their order in `config.json`; a kind two
  of them declare differently keeps the first declaration, and `kgd check`
  reports the conflict), or `null` when it has none;
- `problems`: what `kgd accept` would refuse when given the draft and its
  `with`, empty when it would accept them.

```json
{"drafts": [{"uid": "notes:direct-sum-criterion", "base": "notes", "id": "direct-sum-criterion",
   "path": "/abs/root/.knowledge/drafts/direct-sum-criterion.md", "replaces": null, "foreign": false,
   "class": "node", "kind": "theorem",
   "label": "Direct sum criterion", "aliases": [], "source": "…", "lines": "37-41", "understanding": "yes",
   "epistemic": null, "gloss": "…", "body": "…", "search_terms": "", "evidence": ["…"],
   "out": [{"role": "about", "pos": 0, "uid": "notes:direct-sum", "state": "draft", "label": "Direct sum"},
           {"role": "about", "pos": 1, "uid": "notes:subspace", "state": "accepted", "label": "Subspace",
            "understanding": "yes"}],
   "choices": {"node_kinds": {"definition": [], "theorem": ["about"]},
               "relation_kinds": {"implies": ["premise", "conclusion"]}, "epistemic": ["proved", "stated"]},
   "with": ["notes:direct-sum"], "linked_by": [], "problems": []}],
 "unavailable_bases": []}
```

Drafts sort by base, source, first line and id. `--base` and `--source` (a
base-relative source path prefix) narrow the listing, and `--name` keeps the
drafts whose label or an alias has that name key; each option repeats, ORed
within itself. A draft that cannot be read or parsed is listed with
`label: null` and its problem, unless `--source` or `--name` narrows the
listing. The MCP tool `kg_drafts` returns the same JSON.

### `kgd edit DRAFT FIELD [VALUE]...`

Sets one field of a draft, for the owner's edits from the Capture view or the
terminal; accepted records are edited by drawing them into a draft. FIELD is
`label`, `kind`, `epistemic` or `understanding` (one value; none removes the
last two, and `understanding` `yes` is written as absent), `aliases`,
`requires` or a role (any number of values, each a link for the last two;
none removes the field), or `body`, the prose before `## Search terms` and
`## Evidence` (`-` as its only value reads standard input); `--` before the
values keeps one that starts with `-` from reading as an option. A foreign
draft's `source` takes another web address (one value); a local citation, and
`lines`, change only with `kgd relocate`. Only that field's lines change: a
value is written as a JSON string, a missing field is added before the closing
`---`, and the rest of the file stays as it is. The result must still parse;
whether `kgd accept` would take it is what `kgd drafts` reports. A new label of
a new draft that no draft of any base links renames the draft to the label's
id (numbered like any new id), so the id still describes it. Prints
`{"draft": path, "uid": uid}`, with `renamed_from` when renamed; it takes no
lock, and refuses, writing nothing, when the draft changed meanwhile.

### `kgd accept DRAFT... [--dry-run]`

Each DRAFT is a `.knowledge/drafts/<id>.md` path of a registered base, absolute
or relative to the working directory; drafts of several bases are accepted
together.

1. Take the lock and parse every selected draft.
2. Validate each one in the post-acceptance world: links may resolve to
   accepted records or to other selected drafts, of any base; a link to an unselected draft
   is refused with `select [[x]] too`; a local record's evidence must be fresh.
   A draft with the id of an accepted record replaces it, refused when the
   record's file changed after the draft's (its modification time is later):
   the replacement was drafted from an older record. A draft whose text equals
   its accepted record, drawn from it and left unchanged or left by an accept
   interrupted between link and unlink, is only removed and counts as
   updated.
3. On any problem print `{"refused": [{"path", "message"}]}`, exit 1 and write
   nothing. Problems in other records do not block acceptance.
4. With `--dry-run` print `{"would_create": [uid], "would_update": [uid]}`.
5. Otherwise, in argument order, a new record is `os.link(draft,
   entries/<id>.md)` then `os.unlink(draft)`, and a replacement is
   `os.replace(draft, entries/<id>.md)`. A new record never overwrites
   anything: a target that appeared since validation with different text
   stops the run, and the receipt lists what was already admitted plus
   `aborted: {path, message}`. Rerunning the same command completes an
   interrupted run.
6. Release the lock and run `kgd index` with its embedding phase limited to
   the admitted records: every changed record file is indexed, the new and
   replaced records are embedded, and a record edited since it was embedded
   keeps waiting for the owner's re-embed (docs/retrieval.md, "When records
   are embedded").
7. Print `{"created": [uid], "updated": [uid], "index": {...}}`, where `index`
   is the report of step 6. An embedding failure is reported there and does
   not undo the acceptance; the exit code reflects only refusals and aborts.

### `kgd source SOURCE`

SOURCE is a source file, absolute or relative to the working directory. A
file that is not a source of its base is an error: "is not a source of base
B: sources are its md, tex and txt files outside hidden directories"; so is a
base that registers no document type. It writes nothing and prints the
extraction profiles and inventory agents use:

```json
{"base": "notes", "source": "notes/math/…/01-review.tex",
 "types": [{"type": "math-notes", "node_kinds": {"definition": [], "theorem": ["about"]},
            "relation_kinds": {"implies": ["premise", "conclusion"]}, "epistemic": ["proved", "stated"],
            "guidance": "…"}],
 "type": "math-notes", "node_kinds": {"definition": [], "theorem": ["about"]},
 "relation_kinds": {"implies": ["premise", "conclusion"]}, "epistemic": ["proved", "stated"], "guidance": "…",
 "line_count": 77,
 "markers": [{"base": "notes", "source": "notes/math/…/01-review.tex", "line": 7, "column": 1,
              "name": "Subspace", "text": "", "uid": "notes:subspace"}],
 "records": [{"uid": "notes:subspace", "label": "Subspace", "kind": "definition", "class": "node",
              "lines": "7-19", "understanding": "yes", "gloss": "…"}],
 "drafts": [{"id": "direct-sum-criterion", "label": "…", "kind": "theorem", "class": "node", "lines": "37-41"}]}
```

`types` lists the profile of every document type of the base, in the order
of its `types`; only when the base has one type are that profile's fields
also given at the top level, as above. The agent chooses the type that fits
the document and, in a base of several, declares it in each draft's `type:`.
`markers` are the source's markers with the uid each names, `null` when it
names no record or several. Agents read the source text with their own tools;
the inventory carries no lines. A foreign record is drafted with a type of
the base of the source the owner was reading.

### `kgd markers [SOURCE]... [--uid BASE:ID]... [--base B]... [--orphans]`

Lists knowledge markers with the records they name, read from files: it
writes nothing, takes no lock and opens no database. With SOURCE files (each
a source of a base), their markers; with `--uid` (repeatable), the markers of
that record or draft, found round by round as `check` follows them; with
neither, the markers of every source of the bases, or of those `--base`
names. `--orphans` keeps the markers that name no record or several. It has
no MCP tool.

```json
{"markers": [{"base": "papers", "source": "transformer/transformer_ch.md", "line": 120, "column": 1,
              "name": "Transformer·Learned vs sinusoidal positional encoding",
              "text": "Transformer·Learned vs sinusoidal positional encoding",
              "uid": "papers:learned-vs-sinusoidal-positional-encoding", "candidates": ["papers:learned-vs-sinusoidal-positional-encoding"],
              "path": "/abs/root/.knowledge/entries/learned-vs-sinusoidal-positional-encoding.md"}],
 "total": 1}
```

`column` is 1-based; `text` is what the marker shows (its NAME unless it
gives a TEXT; in tex the braces' content, possibly empty); `candidates` are
every uid the NAME may mean, and `uid` and `path` the one it names, or
`null`.

### `kgd mark DRAFT [--name NAME] [--dry-run]`

Writes a marker naming a local draft into its source, on the line of its
first Evidence quote, under the home lock; it is the one command that writes
a source file, and it changes only that line, never the line count.

- In md and txt it writes `--[[NAME]]--:` where the quote starts (after any
  list or quote prefix, and at the line's start when the quote starts inside
  inline math), followed by a space when text follows, so the marker introduces the passage it names. When the quote
  starts on a heading, table, fenced code, display math, HTML or frontmatter
  line, the marker goes on the nearest blank line above instead.
- In tex it writes `\kn[NAME]{}` where the quote starts, which prints
  nothing.
- NAME defaults to the draft's label, or its id when the label holds one of
  `[ ] | { }`. It must name the draft (by the resolution rule of
  [Knowledge markers](#knowledge-markers)); otherwise the command refuses and
  asks for `--name` with the id.
- It refuses a foreign draft, an accepted record, a first quote that does not
  occur in the cited lines and a source that changed while it was written.
  When the source already has a marker naming the draft, it writes nothing
  and prints `{"marked": uid, "already": [...]}`.

It prints `{"marked": uid, "source", "line", "column", "marker"}`, with
`"dry_run": true` and nothing written under `--dry-run`.

### `kgd find TEXT [--base B]... [--source PREFIX]... [--limit 20]`

TEXT is the text to find, or `-` to read it from standard input. It reads the
sources of the available bases (narrowed by `--base` and by
`--source`, base-relative path prefixes) and writes nothing:

```json
{"matches": [{"base": "papers", "source": "papers/mae/mae_ch.md", "lines": "65-65", "exact": false,
              "text": "我们将 image 划分为规则、不重叠的 patches"}],
 "total": 1}
```

Text and sources are compared as tokens: NFKC-casefolded runs of letters and
digits, each CJK character a token of its own; line breaks and punctuation
never matter. A text of eight tokens or more matches a span that holds its
tokens in order with at most a quarter as many other tokens between them, so
a passage copied from a reading view, which hides link targets and markup,
finds its lines; a shorter text, such as a name, matches its tokens exactly in
sequence. `exact` says no token was skipped. `text` is the source's own text
of the match, from its first token to its last, with the closing punctuation
that follows, cut at 2000 characters. Matches are listed by base, source and
line, at most `--limit`; `total` counts them all. The MCP tool `kg_find`
returns the same JSON.

### `kgd relocate TARGET [TEXT] [--url URL] [--base B]... [--source PREFIX]... [--dry-run]`

TARGET is `base:id` of an accepted record, or the path of a record or draft
file; TEXT is the passage, or `-` to read it from standard input.

1. Take the lock and parse the record.
2. Find where it goes:
   - With TEXT, find it as `kgd find` does in the sources of every available
     base, narrowed by `--base` and `--source` (both repeatable). Unless it
     occurs exactly once, print `{"refused": {"message", "matches"}}`, exit 1
     and write nothing.
   - Without TEXT, follow the record's markers in three rounds as `check`
     does. Refuse with "no marker names the record; pass the passage's text,
     or --url and the page's text to make it foreign" when none does, and,
     when the markers no longer lead to quotes that hold, with "pass the new
     passage's text" and the `markers` found.
   - With `--url URL` and TEXT, the page's own text, the record goes to that
     web address and becomes foreign; no source is searched.
3. Rewrite the frontmatter's `source:` line and its `lines:` line (added
   after `source:` for a foreign record, removed for `--url`); every other
   byte stays. Without TEXT the Evidence stays. With TEXT, when the record's
   quotes all lie in the matched text, or in its lines, the Evidence stays;
   otherwise it becomes the matched text, one quote per run of non-blank
   lines. With `--url` the Evidence becomes TEXT.
4. When the match lies in another base, the record moves there with its id:
   an accepted record into that base's `entries/`, a draft into its
   `drafts/`. Its own link values are rewritten for the new base (a link into
   it becomes local `"[[id]]"`, any other `"[[base:id]]"`), and so is every
   role and `requires` value linking it in every base's records and drafts;
   body-prose wikilinks are not touched. Refuse when that base already has a
   record or draft with the id, or while a replacement draft of the accepted
   record waits.
5. Refuse when the result would not pass `check` (its kind, roles and
   evidence against the new source; links and the label are not this
   command's concern) or when a file to write changed meanwhile. `--dry-run`
   prints the report without writing.
6. Write each file through a temp file and an atomic replace, remove the old
   file of a moved record, release the lock, and, when an accepted record was
   written, run `kgd index --no-embed`. Print `{"relocated": uid, "path",
   "from": {source, lines}, "to": {source, lines}, "evidence":
   "kept"|"replaced", "index": {...}}`, where `relocated` is the record's new
   uid and `to.lines` is empty for a web address, plus, for a move,
   `moved: {from, to}` (the old and new uid) and `relinked` (the files whose
   links were rewritten).

### Changes, deletes and identity changes of accepted records

When to ignore, replace or relocate knowledge that is already recorded
follows [Conflicts, updates and relocation](#conflicts-updates-and-relocation);
this section is how such a change is written.

- **Replacement.** The record is drawn into a draft with its id (the owner in
  the Capture view, or an agent from a fresh read of the record), changed
  there, and accepted. The owner may also edit accepted records by hand; then
  the owner runs `kgd check --base B`, and `kgd index --no-embed` (the
  Obsidian plugin runs it whenever a record file is saved). A better
  name goes into `label` or `aliases`; the id stays.
- **Delete.** The owner removes the file. `kgd check` then lists every inbound
  link left dangling, in this base and others; each is removed from its
  record or draft, or repointed by meaning to another record. Body-prose
  wikilinks to it, which `kgd check` does not validate, become plain text.
  Run `kgd check` and `kgd index --no-embed`.
- **Relocation.** `kgd relocate`, within one base or into another, where it
  also rewrites every link to the record, or to a web page with `--url`.
- **Identity change.** Ids are permanent, so a changed identity is a one-time
  link rewrite, never a rename. Merging true duplicates keeps one record;
  splitting a record that conflated two meanings drafts new records for them.
  Once the owner accepts the change, every inbound link is repointed by
  meaning, including body-prose wikilinks that `kgd check` does not validate,
  the superseded record is deleted, and `kgd check --base B` and
  `kgd index --no-embed` run.

### Base registration

`kgd base add PATH [--name N] [--type T]...` creates the home on first use
(`config.json` as `{"bases": {}, "embedding": null}`, an empty `types/` and
the `.gitignore`), registers the directory with the document types `--type`
names (each a file of `types/`; none leaves `types` empty, to fill in
`config.json`) and creates `<root>/.knowledge/entries/`. The default name is
the directory's basename, never transformed. `kgd base rm NAME` removes
the registration only and adds `dangling`, the `[[NAME:…]]` values left in the
other bases' entries and drafts as `{path, role, value}`. `kgd base list`
prints each base's `name`, `path`, `root`, `types`, `available`, `records` and `drafts`
(file counts, `null` when unavailable), `indexed` and `lag`. There is no
`base rename`: a base name is a permanent citation key, and removing a base
then adding the same directory under another name leaves every
`[[old-name:…]]` link dangling.
