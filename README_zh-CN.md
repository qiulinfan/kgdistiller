<div align="center">

<h1>kgdistiller</h1>
<p><strong>放在 Obsidian vault 里的本地科研知识库，每条记录都有出处。</strong></p>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab)](pyproject.toml)
[![Obsidian desktop 1.13.7+](https://img.shields.io/badge/Obsidian%20desktop-1.13.7%2B-7c3aed)](manifest.json)
[![Plugin release](https://img.shields.io/github/v/release/qiulinfan/kgdistiller?label=Obsidian%20plugin)](https://github.com/qiulinfan/kgdistiller/releases)
[![CI](https://github.com/qiulinfan/kgdistiller/actions/workflows/ci.yml/badge.svg)](https://github.com/qiulinfan/kgdistiller/actions/workflows/ci.yml)

[English](README.md) · **简体中文**

</div>

## 主要功能

- 定义、结论以及它们之间的关系都存成 Markdown 记录，和所引用的论文、笔记放在一起。每条记录带原文引文和行号范围。
- 一条关系可以按 role 绑定任意多个参与者。kind 和 role 由你按 document type 自己声明。
- 通过一个 SQLite 索引同时搜索所有已注册的 vault；`kgd search` 融合全文检索、dense embedding 检索（例如 BGE-M3）和名称匹配三路结果。
- 在 Obsidian 里选中文字即可查询知识，看到记录周围的类型化邻居图；内置 agent 把选中内容起草成草稿，你在入库侧栏里确认入库；coding agent 通过 Skills 和 MCP server 来记录、编译和查询知识。

本仓库包含 Obsidian 插件和 Python 核心，二者都是 **3.0.0**。两者分开发版、分开打 tag：插件 tag 是 `x.y.z`，核心 tag 是 `core-x.y.z`。

## 为什么以知识图谱为核心

> 我们这个 research oriented 的知识库，我之所以把知识图谱作为其最根本的核心，本质上是我认为：
>
> 一篇知识密集、研究性的知识源并不是每段文字都是关键，有很多东西是规范性目的或者“向读者证明我这个研究/架构有意义”。但是，它们进入我的眼睛的时候，我早就确认了它们是有意义的，因而我们真正需要的知识仅仅只是这个文章的一些知识节点而已。
>
> 这个 philosophy 是这个图谱的关键，因而检索“到底在哪篇文章的哪个段落”是完全依赖知识图谱的，向量和 bm25 检索也仅仅针对每个知识节点。

Agent 可以先检索记录，再阅读原文上下文：

```sh
kgd get research:sigma-algebra --source-lines 20
```

这会实时读取被引用的行段，以及前后各 20 行。需要更多上下文时，agent 根据 `kgd base list` 返回的 base 根目录和记录中的相对 `source` 路径读取原文件。上下文保留在来源中；现有的 base、路径、行号和 evidence 已经提供了这条连接。

## Obsidian 插件

打开 [kgdistiller 社区页面](https://community.obsidian.md/plugins/kgdistiller)，点 **Add to Obsidian**，然后启用 **kgdistiller**。手动安装时，从 [plugin release](https://github.com/qiulinfan/kgdistiller/releases) 下载 `main.js`、`manifest.json`、`styles.css`，放进 `<vault>/.obsidian/plugins/kgdistiller/`，重载 Obsidian 后启用。需要桌面版 Obsidian **1.13.7 或更新**，移动端不加载此插件。

记录存放在 vault 的隐藏目录 `.knowledge/` 里，Obsidian 默认会跳过它。打开 **Settings → kgdistiller → Index hidden knowledge folder** 之后，`.knowledge/` 会像普通文件夹一样出现在文件列表、搜索、Properties 和 Backlinks 中。按[安装](#安装)装好 CLI 后，`kgd obsidian install` 会把 CLI 随包附带的插件复制进 vault，并一起打开这个设置。它具体改动哪些文件，见 [docs/obsidian.md](docs/obsidian.md#installation)。

在 **Settings → kgdistiller → kgd executable** 填好 `kgd` 的路径后：

- **检索侧栏**：选中文字后按住 ⌘（其他系统是 Ctrl）0.3 秒，或运行 **Look up selection**，或在侧栏的搜索框里输入（ribbon 图标 **Open kgdistiller** 打开它）。它在所有已注册的 base 里查询，显示同名记录、当前聚焦的记录及其 3 步以内的类型化邻居图，以及相关记录。在编辑器里打开一条记录或一个 source，侧栏也会跟着聚焦。搜索框下面的 **Re-embed** 按钮在有记录等着重算向量时是实的，并显示条数。
- **Agent**（可选）：在 **Model providers** 里配好模型服务后，侧栏能判断选中的内容是否已入库，结合知识库、论文和网页回答问题，查一个术语的出处（**Find sources**），并把选中内容起草成草稿（**Capture**）。
- **入库侧栏**：草稿在检索侧栏下方的 **kgdistiller Capture** 视图里等你确认，左侧按来源文件列出（外部来源归在 foreign 下），每条都完整显示，并标明 `kgd accept` 能否接受。**Accept** 把草稿连同它链接的草稿一起入库，并建索引、算向量；**Open draft** 打开文件修改；**Discard** 确认后删除。不点 Accept，什么都不会进入知识库。
- **知识标记**：md 或 txt 笔记里的 `--[[Sigma-algebra]]--`、tex 文件里的 `\kn{Sigma-algebra}` 这样的标记，指明这段原文陈述的是哪条记录；capture 会为每条本地草稿写一个。标记在 live preview、阅读视图和 latex-live 的 TeX 编辑器里显示为紫色标签；Cmd/Ctrl+点击打开对应记录，**Jump between knowledge and its marker** 在记录和标记之间来回跳转。
- **Embedding 设置**：**Settings → kgdistiller → Embedding** 显示 home 的 embedding 模型，并通过 `kgd embedding` 修改。

改记录用 Properties 面板；保存后关键词索引会自动刷新，改过的记录在你点 Re-embed 时才重算向量。完整说明见 [docs/obsidian.md](docs/obsidian.md)。

## 隐私与数据边界

插件通过你配置的 `kgd` 读取知识，自己从不打开 home 或数据库。它写自己的 `data.json`（API key 存在 Obsidian 的 SecretStorage 里，`data.json` 只存它们的名字）；你起草、入库或丢弃时，它会写 `.knowledge/drafts/` 下的草稿。它用你配置的 `kgd` 运行 `kgd base list`、`kgd source`、`kgd markers`、`kgd mark`（把刚起草的草稿的标记写进它的 source）、`kgd edit`、`kgd accept`、`kgd relocate`（localize 一条记录时运行，会改变已接受记录的引用位置，也可能把它移到另一个 base）、`kgd index`、`kgd index --no-embed` 和 `kgd embedding`。它不收集遥测，不需要账号，也不安装或更新任何东西。不用 agent 时，查询不发任何网络请求。agent 会把选中的文字、上下文和它读到的记录发给你选的模型服务；网页和论文搜索发往 Exa（`mcp.exa.ai`）；**Find sources** 还会读 Wikipedia 和 arXiv。为了索引隐藏目录，插件 patch 了 Obsidian 内部未公开的 file-adapter 方法，并用 Node 的文件系统 API 检查 vault 中 `.knowledge/` 下的路径，所以只支持桌面端。这样做也会让 `.knowledge/` 对 Obsidian 搜索和同一 vault 的其他插件可见。如果启用了 Hidden Folders Access 插件，kgdistiller 会报告冲突，并关闭自己的隐藏目录索引。

CLI 和 MCP server 都在本地运行。它们读取 home 中的 `config.json` 和 `types/`、各 base 的记录和草稿，以及各 base 的 source 文件（用于核对 evidence、查找知识标记和 `kgd get --source-lines`）。它们只写这些位置：

- home：`kgd base add`/`rm` 和 `kgd embedding` 修改的 `config.json`，第一次 `kgd base add` 创建的 `types/` 目录和 `.gitignore`，以及 `index.sqlite*` 和 `lock`；
- base 的 `.knowledge/` 下的 `entries/` 和 `drafts/` 目录；
- source 文件中的一行：`kgd mark` 在这里插入草稿的标记；
- vault `.obsidian/` 中的插件目录、`community-plugins.json` 和插件的 `data.json`，由 `kgd obsidian install` 写入；
- 各 agent runtime 的 home，由 `kgd claude link` 和 `kgd codex link` 写入。

MCP server 只走 stdio，没有写工具。

核心唯一的联网是从 Hugging Face Hub 下载 embedding 模型：`kgd embedding set` 把它下载到 Hugging Face 缓存（`BAAI/bge-m3` 约 2.3 GB）；之后如果没有设置 `HF_HUB_OFFLINE=1`，加载时仍可能访问 Hub，插件对其余所有调用都会设置它。不发送 token，也不运行远程代码。模型在你的电脑上、在一个共享进程里运行，记录原文不会为了算向量离开本机。kgdistiller 不会替你 commit 或 push，每个 vault 和 home 请放在你自己的 Git 仓库里管理。

## 开源许可

kgdistiller 原创代码采用 [MIT](LICENSE)，版权 2026 Qiulin Fan，允许商业使用、修改和再分发，需保留版权和许可声明。插件 bundle 里包含 Cytoscape.js、cytoscape-fcose、cose-base 和 layout-base，均为 MIT；隐藏目录索引改编自 Hidden Folders Access 2.1.1（MIT）。完整声明已嵌入 bundle，并列在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 核心概念

kgdistiller 面向学术研究：读的论文、写的论文、自己的研究笔记。每个注册的目录（通常是一个 Obsidian vault）叫一个 base。base 把知识存成 Markdown 记录：已接受的记录在 `.knowledge/entries/`，等你确认的草稿在 `.knowledge/drafts/`。记录分两类：node（一个概念，或一条表述精确的结论，用 role 链接它所涉及的记录），以及 relation（用 role 键绑定至少两个值）。每条记录都引用本 base 里的一个 source 文件，写明路径、行号范围和原文引文；foreign（外部）知识则引用外部作品的网页地址和该网页的原文。一个 base 的 source 是它在隐藏目录之外的所有 `.md`、`.tex` 和 `.txt` 文件；kgdistiller 只把它们当成带行号的文本来读，除了指明记录位置的知识标记，不解析其中任何语法。

全局 home 是 `$KGDISTILLER_HOME`（默认 `~/.knowledge`），负责注册 base 及你为它定义的 document type，并保存派生数据库 `index.sqlite`：

```text
~/.knowledge/                      # $KGDISTILLER_HOME
├── config.json                    # bases、它们的 document types、embedding 模型
├── types/<name>.md                # 每个 document type 一个文件
├── .gitignore                     # "index.sqlite*" 和 "lock"
├── index.sqlite                   # 派生数据，由 `kgd index` 重建
└── lock

~/research/                        # 一个 base，通常是 Obsidian vault
├── notes/                         # sources：所有 .md、.tex 和 .txt 文件
└── .knowledge/
    ├── entries/<id>.md            # 已接受的记录
    └── drafts/<id>.md             # 草稿，格式与记录相同
```

数据库里有 FTS5 全文索引行和 float32 向量，向量用 NumPy 精确扫描。embedding 模型以 int8 权重运行，每个用户只有一个共享进程，`BAAI/bge-m3` 约占 1 GB，空闲 15 分钟后退出。`kgd search` 用 reciprocal rank 融合三路：lexical、dense，以及对 label 和 alias 的名称匹配。如果上次 `kgd index` 之后记录文件有改动，每次读取都会在输出里报告 `lag`。知识本身是记录文件，数据库随时可以从它们重建。

## 安装

打包需要 Node 22 和 npm：插件的 `main.js` 从源码构建，不进版本库。所以直接 `uv tool install git+https://…` 会失败，要从 checkout 安装：

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
npm run build
uv tool install '.[retrieval]'
uv tool update-shell
```

这会在 Windows、macOS 和 Linux 上装好 `kgd` 和 `kgdistiller` 两个命令（同一个程序）。如果 `kgd` 还不在 `PATH` 上，重开一个 shell。`retrieval` extra 带来 dense 检索所需的 PyTorch、transformers、sentencepiece 和 NumPy。不装它（`uv tool install .`）时，home 配置里保持 `"embedding": null`，搜索只用 lexical 和名称两路。

## 快速开始

注册一个 base。目录必须已经存在，可以用 `mkdir -p ~/research/notes` 创建。后面的命令还需要 `notes/measure.md`，它的第 3 到 4 行必须包含[记录格式](#记录格式)里 sigma-algebra 例子引用的原文。第一次 `base add` 会创建 home：

```sh
kgd base add ~/research --name research
```

在 `~/.knowledge/types/research-notes.md` 定义一个 document type。frontmatter 把每个 node kind 和 relation kind 映射到它的 role（有顺序；node kind 可以没有 role）；正文是 agent 从这类文档里抽取记录时遵循的指引。产品不会自动启用 type；你可以自己写，也可以修改[起始模板](#文档类型与起始模板)。

```markdown
---
node_kinds:
  definition: []
  theorem: [about]
  example: [uses, setting]
relation_kinds:
  implies: [premise, conclusion]
epistemic: [proved, stated]
---
Extract each definition and each precisely stated result as a node; a result
links every record it is about under about. Record a relation only when the
text states a connection between records, such as one result implying
another.
```

在 `~/.knowledge/config.json` 里给 base 指定这个 type（type 文件写好后，`kgd base add ~/research --name research --type research-notes` 也会写入）。base 中隐藏目录之外的每个 `.md`、`.tex`、`.txt` 文件都是 source，不需要逐个注册：

```json
{
  "bases": {
    "research": {
      "path": "~/research",
      "types": ["research-notes"]
    }
  },
  "embedding": null
}
```

然后选择 embedding 模型。`kgd embedding set` 会下载它并检查 kgdistiller 能否运行；它必须是自带 `sentencepiece.bpe.model` 的 XLM-RoBERTa 句向量模型，例如 `BAAI/bge-m3`。不设置就只用关键词和名称检索：

```sh
kgd embedding set BAAI/bge-m3
```

然后添加记录。可以让装好 Skills 的 agent 记录一个定义，或编译一整篇笔记；结果都是等你审阅、确认的草稿。手动添加时，先用 `kgd source` 打印 source 所在 base 的各个 type profile，再在 `~/research/.knowledge/drafts/` 里写一份草稿（格式见[记录格式](#记录格式)），然后检查、接受并建索引：

```sh
cd ~/research
kgd source notes/measure.md
kgd check
kgd accept .knowledge/drafts/sigma-algebra.md
kgd search "sigma algebra"
```

`kgd source` 打印该 source 所在 base 每个 document type 的 profile、它的知识标记，以及已有的记录与草稿。`kgd check` 校验 home、记录和草稿，并报告位置移动了或与 source 对不上的 evidence。`kgd accept` 把草稿移进 `entries/`，要么全部成功，要么什么都不改，然后为它们建索引、算向量；id 与已接受记录相同的草稿会替换那条记录。所有命令都输出 JSON，退出码：成功 0，有问题或被拒绝 1，用法错误 2。

## 文档类型与起始模板

> 我之前有另一个重要的设计：agent 并不懂你要什么样的知识节点，因而我认为一个知识源文章属于某个类型（比如 math notes、ai research），用户自己为这个类型去制定知识节点类型以供 agent 去提取。
>
> 我作为仓库创始人自己需要的类型（比如 math notes、ai research、cs notes），直接作为默认 templates 就可以了。

一个 document type 包含 node kind、relation kind 及其 role、可选的 epistemic 标签，以及正文中的抽取指引。base 在 `config.json` 中列出它的 type；每条记录在 `type:` 中写明自己遵循哪一个（base 只有一个 type 时可以省略），由起草它的 agent 按文档选择合适的 type。

Capture 和 compile 在提出记录之前，通过 `kgd source SOURCE` 读取这份策略；`kgd check` 和接受流程根据它校验 kind 与 role。你可以修改策略或增加类型，无需修改引擎。

模板：

| 模板 | 保留的知识 |
|---|---|
| [math-notes](templates/document-types/math-notes.md) | 定义、公理、精确陈述的定理，以及推导关系和例子。 |
| [ai-research](templates/document-types/ai-research.md) | 有定义的概念、算法、架构、目标函数、精确结果和实验发现，以及比较与蕴含关系。 |
| [cs-notes](templates/document-types/cs-notes.md) | 定义、算法、数据结构、架构、复杂度界等结果和不变量，以及实现关系。 |

把选择的文件复制到 `$KGDISTILLER_HOME/types/`，修改其中的分类、角色和指引，再把文件名加进使用它的 base 的 `types`。它们是起始模板；安装和 `kgd base add` 都把正式启用的类型目录留给你定义。Python 包中也附带这些模板，位于 `kgdistiller/product/templates/document-types/`。

## 记录格式

一条记录就是一个文件，文件名（不含扩展名）就是 id：可读的 slug，最长 80 个字符，允许中日韩文字。全局 uid 是 `<base>:<id>`。一个 node：

```markdown
---
label: Sigma-algebra
kind: definition
aliases:
  - σ-algebra
source: notes/measure.md
lines: 3-4
---
A sigma-algebra on a set X is a collection of subsets of X closed under complements and countable unions.

## Evidence

> A sigma-algebra on a set X is a collection of subsets of X that contains X
> and is closed under complements and countable unions.
```

一个 kind 为 `implies` 的 relation，role 来自 document type，引文本身陈述了这条关系：

```markdown
---
label: Hahn decomposition implies Jordan decomposition
kind: implies
premise:
  - "[[hahn-decomposition-theorem]]"
conclusion:
  - "[[jordan-decomposition-theorem]]"
epistemic: stated
source: notes/measure.md
lines: 41
---
The Hahn decomposition theorem implies the Jordan decomposition theorem.

## Evidence

> The Jordan decomposition theorem follows from the Hahn decomposition theorem.
```

- 必填：`label`、`kind`、`source`（相对 base 根目录的路径）和 `lines`（`a` 或 `a-b`）。label 在所有 base 中只指一个概念：名字已被占用、意义不同的新概念，在名字后加上来源文件名，例如 `Autoencoder (Autoencoder&VAE)`。foreign 记录的 `source` 是它引用的外部作品的网页地址，没有 `lines`。可选：`aliases`、`understanding`（`yes` 或 `no`，默认 `yes`）、`epistemic`、`requires`。`tags` 和 `cssclasses` 允许出现，但会被忽略。`type` 写明记录的 document type，base 有多个 type 时必填。
- 其余键都是记录 kind 的 role。记录是 node 还是 relation，取决于它的 kind 在 document type 里属于哪一节。relation 至少绑定两个值，role 数量不限；声明了 role 的 node kind（比如 theorem 的 `about`）至少链接一条记录。
- 列表里的值都是链接：`"[[id]]"`、`"[[base:id]]"` 或 `"[[.knowledge/entries/id]]"`。库里还没有的概念，先单独入库（从本地 source，或作为 foreign 知识），再链接。
- 正文依次是说明文字、可选的 `## Search terms` 小节，最后是 `## Evidence` 小节，内容是从被引用行（或网页）逐字摘出的引文。
- 定理、发现和例子都是 node，用 role 链接它们涉及的记录，比如 `example: [uses, setting]`；“两个子空间的和是子空间”是一条涉及两条记录的 theorem，不是 relation。

agent 只写草稿，由你确认。已接受的记录可以手动原地修改，改完运行 `kgd check` 和 `kgd index --no-embed`（插件会自动刷新关键词索引）；文本变了的记录会失去向量，在你用 `kgd index` 重算之前，dense 检索找不到它。编辑 source 后，被引用的内容可能挪到了别的行。这时运行 `kgd check --fix-lines`：如果某条记录的引文在 source 的别处能找到，而且只出现一次，它就把这条记录的 `lines:` 改到新位置。文件改名或移动后，`kgd check` 沿每条记录的知识标记查找（先是它自己的文件，再是它的 base，最后是其他所有 base），在 `follow` 中报告它们指向哪里；`--fix-lines` 应用同一 base 内的结果，`kgd relocate UID` 可应用任何一个。完整语法和校验规则见 [docs/model.md](docs/model.md)。

## 命令

| 命令 | 作用 |
|---|---|
| `kgd base add PATH [--name N] [--type T]...`、`base rm NAME`、`base list` | 注册（连同其记录可用的 document type）、注销和列出 base，附带 type、记录数、草稿数和索引数。 |
| `kgd embedding`、`embedding set MODEL`、`embedding off` | 显示 home 的 embedding 模型和向量状态；下载、检查并启用一个模型；或关闭 dense 检索。 |
| `kgd check [--base B] [--fix-lines]` | 校验 home、记录和草稿，报告移动或过期的 evidence，以及知识标记指向的新位置（`follow`）。 |
| `kgd source SOURCE` | 打印某个 source 所在 base 的 document type profile、它的知识标记，以及引用它的记录和草稿。 |
| `kgd markers [SOURCE...] [--uid UID] [--base B] [--orphans]` | 列出知识标记及其指向的记录。 |
| `kgd mark DRAFT [--name NAME] [--dry-run]` | 把指向一条本地草稿的标记写进它的 source，写在第一条引文所在的行，不改变行数。 |
| `kgd find TEXT [--base B] [--source PREFIX]` | 在 source 里查找一段文字或一个名字，不受换行和阅读视图隐藏的标记影响。 |
| `kgd relocate TARGET [TEXT] [--url URL] [--base B] [--source PREFIX]` | 把一条记录的 source、行号和引文移到 source 里唯一包含这段文字的段落；不给 TEXT 时移到它的知识标记指向的位置；段落在另一个 base 时，把记录移过去，并改写所有指向它的链接；给出 `--url` 和网页原文时，把它改成 foreign。 |
| `kgd drafts [--base B] [--source PREFIX] [--name NAME]` | 列出草稿、它们的链接，以及 `kgd accept` 会拒绝的原因；不写任何东西。 |
| `kgd edit DRAFT FIELD [VALUE...]` | 设置草稿的一个字段：label、kind、epistemic、understanding、aliases、requires、某个 role，或正文陈述。 |
| `kgd accept [--dry-run] DRAFT...` | 把草稿转为新记录，或替换 id 相同的已接受记录，全部成功或全部不改，然后建索引并算向量。 |
| `kgd index [--rebuild] [--no-embed]` | 让数据库与记录文件同步；`--no-embed` 只刷新关键词索引。 |
| `kgd search QUERY [--limit N] [--no-dense]` | 按 lexical、dense、名称三路给记录排序。 |
| `kgd resolve TERM...` | 列出 term 的各个义项和提及。 |
| `kgd get UID... [--source-lines N]` | 读取完整记录及其链接，可附带 source 中当前的被引用行。 |
| `kgd neighbors UID... [--role R] [--dir out\|in\|both] [--depth N]` | 沿链接展开：依赖闭包；`--dir in` 列出链接某条记录的陈述 node 和 relation。 |
| `kgd browse [HANDLE]` | 列出 base、某个 base 的 source 目录、某个 source 按 kind 分组的记录，或用 `--kind` 列出某类全部记录。 |
| `kgd pack UID... [--budget BYTES] [--requires-depth N]` | 在字节预算内打包完整记录，附带链接其中多条记录的记录和未补上的 gap。 |
| `kgd obsidian install [--base NAME]` | 在某个 base 的 vault 中安装或更新插件，并启用隐藏目录索引。 |
| `kgd claude link\|doctor`、`kgd codex link\|doctor` | 为 Claude Code 或 Codex 安装或检查 Skills、agent presets 和 workflow 文件。 |
| `kgd mcp` | 通过 stdio 为所有 base 提供只读工具。 |

`search`、`resolve`、`neighbors`、`browse` 和 `pack` 支持筛选参数 `--base`、`--kind`、`--class {node,relation}`、`--source PREFIX` 和 `--understanding`，都可以重复使用。id 在所有 base 中唯一时可以直接写 id，不带 base 前缀。各命令细节看 `kgd <command> --help`，输出格式见 [docs/retrieval.md](docs/retrieval.md)。

## 审阅草稿

每次 capture 和 compile 的结果都是草稿。可以在插件的 Capture 视图里审阅：它按来源列出草稿，显示每条的链接，以及 `kgd accept` 会拒绝的原因。也可以像普通文件一样打开草稿，然后运行：

```sh
kgd drafts --source notes/measure.md
kgd accept .knowledge/drafts/measure-space.md
```

草稿的字段和链接可以在它的 Capture 视图卡片上修改，也可以用 `kgd edit` 修改。`kgd accept` 只接受给出的那些草稿，要么全部成功，要么什么都不改，并为它们建索引、算向量。如果某条草稿链接到一条你没给出的草稿，它会拒绝执行、不做任何改动，并给出类似 `select [[sigma-algebra]] too` 的提示：把那条草稿也一起给出（Capture 视图会把一条草稿链接的草稿一起传入）。不要的草稿，直接删掉文件即可。

## Agent 集成

五个 Skill 覆盖写入和查询流程：

| Skill | 作用 |
|---|---|
| `capture-kgdistiller` | 边读边把一条知识起草成草稿（概念、source 陈述的结论，或记录之间的关系），带上引文；借用的概念则从其出处网页起草成 foreign 知识；本地草稿会在 source 中写上标记。`kgdistiller-capture-writer` agent preset 在后台运行它。 |
| `compile-kgdistiller` | 按适合该文档的 document type，从一个 source 的一部分或全部起草草稿，并在 source 中写上标记。已记录的知识按 [docs/model.md](docs/model.md#conflicts-updates-and-relocation) 的冲突规则忽略，或起草替换草稿。 |
| `relocate-kgdistiller` | source 改动、改名或移动后，沿记录的知识标记依次在它自己的文件、它的 base 和其他所有 base 中找到它的去处并移过去；按意义判断改写过的段落；把记录移到你贴出的段落；没有标记、哪里都找不到的知识改成 foreign，引用陈述它的网页。 |
| `query-kgdistiller` | 用 search、resolve、get、neighbors、browse 和 pack 从知识库作答，引用 `source:lines`。只读，配有 `kgdistiller-query-reviewer` agent preset。 |
| `deploy-kgdistiller` | 安装 kgdistiller，注册 base 及其 document type 和模型，然后检查、建索引、安装插件、链接 runtime。 |

Skill 只写草稿；你确认入库时才建索引、算向量，你手动改过的记录何时重算向量由你决定。按你用的 runtime 安装：

```sh
kgd claude link
kgd claude doctor
```

```sh
kgd codex link
kgd codex doctor
```

`link` 把 Skills、`kgdistiller-query-reviewer` 和 `kgdistiller-capture-writer` 两个 preset 以及 workflow manifest 装到 `~/.claude` 或 `~/.codex`（遵循 `CLAUDE_CONFIG_DIR` 和 `CODEX_HOME`），不会修改 `CLAUDE.md`、`settings.json`、`AGENTS.md` 或 `config.toml`。OMP 和 dsh（DeepSeek Harness）只装 Skills，在 checkout 里运行 `./scripts/link-skills.sh omp` 或 `./scripts/link-skills.sh dsh`（PowerShell 7：`./scripts/link-skills.ps1 -Runtime dsh`）。

MCP server 提供 `kg_search`、`kg_resolve`、`kg_get`、`kg_neighbors`、`kg_browse`、`kg_pack`、`kg_drafts` 和 `kg_find`，JSON 与 CLI 相同。它自己不加载模型：dense 检索交给共享的 embedding 进程，只有空闲 15 分钟后的第一次 dense 搜索需要等模型加载。注册命令是 `kgd mcp`：

```sh
claude mcp add kgdistiller -s user -e HF_HUB_OFFLINE=1 -- kgd mcp
codex mcp add kgdistiller --env HF_HUB_OFFLINE=1 -- kgd mcp
```

OMP 原生从 `.mcp.json`、`.omp/mcp.json` 或 `~/.omp/agent/mcp.json` 加载 stdio MCP server，在 OMP 里执行 `/mcp add kgdistiller -- kgd mcp` 就会写入配置。使用 `omp -p` 时设置 `OMP_MCP_REQUIRE_READY=1`，让第一轮等 server 就绪。详见 [docs/deployment.md](docs/deployment.md#mcp-server)。

## 让 agent 安装

让 agent 从本仓库安装 kgdistiller 时，它应当：

1. clone 仓库，运行 `npm run build`、`uv tool install '.[retrieval]'` 和 `uv tool update-shell`，确认 `kgd --help` 能运行。
2. 链接自己的 runtime：先运行 `kgd claude link` 再运行 `kgd claude doctor`，或先 `kgd codex link` 再 `kgd codex doctor`；OMP 或 dsh 用 `./scripts/link-skills.sh omp|dsh`。完成后开一个新 session。
3. 按 `deploy-kgdistiller` Skill 操作：为每个 vault 运行 `kgd base add`，把 document type 写进 `~/.knowledge/types/`、把每个 base 的 `types` 写进 `~/.knowledge/config.json`，用 `kgd embedding set BAAI/bge-m3` 设置模型，再运行 `kgd check`、`kgd index`；在 Obsidian 中打开过的 vault 还要运行 `kgd obsidian install`。
4. 用 `kgd base list` 和 `kgd search "KNOWN NAME" --base NAME` 做冒烟测试。

之后，“把这个定义记进我的 kgd 知识库”或“在知识库里搜 measure space”这类请求就会交给对应的 Skill。

## 备份与恢复

每个 base 的 source 和 `.knowledge/entries/`、`drafts/` 一起提交。home 的 `config.json`、`types/` 和 `.gitignore` 单独放一个私有仓库；`index.sqlite*` 和 `lock` 已被忽略。换新机器时，恢复这些文件，改好每个 base 的 `path`，然后重建：

```sh
kgd check
kgd index
```

数据库缺失或损坏时，`kgd index` 会从记录文件重建它，并重新 embed 所有记录。事先删除数据库（`rm -f ~/.knowledge/index.sqlite*`）不是必需的。embedding 进程在 Apple silicon 上用 `BAAI/bge-m3` 每秒约编码 14 条。`kgd index --rebuild` 重新生成所有行，但不重新 embed。

## 开发

```sh
npm run build
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv build --out-dir build/release/3.0.0
uv run --locked python scripts/check_distribution.py --dist-root build/release/3.0.0
npm test
(cd integrations/obsidian && npm ci && npm run check)
```

根目录的 `npm run build` 必须先跑：全新 checkout 里每个 `uv run` 和 `uv build` 都需要已构建的插件。测试使用临时 `KGDISTILLER_HOME` 和假 encoder。发布检查项（包括可选的真实模型测试）见 [docs/release.md](docs/release.md)。

延伸阅读：[docs/model.md](docs/model.md)（记录与写入流程）、[docs/retrieval.md](docs/retrieval.md)（数据库、搜索和 MCP 工具）、[docs/obsidian.md](docs/obsidian.md)（插件）、[docs/deployment.md](docs/deployment.md)（安装、配置、恢复）、[docs/product-workflows.md](docs/product-workflows.md)（Skills 与 workflow）。
