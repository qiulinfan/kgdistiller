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
- 在 Obsidian 里审阅、修改记录，有 typed graph 视图；coding agent 通过 Skills 和 MCP server 来记录、编译和查询知识。

本仓库包含 Obsidian 插件（**0.1.5**）和 Python 核心（**0.4.0**）。两者分开发版、分开打 tag：插件 tag 是 `x.y.z`，核心 tag 是 `core-x.y.z`。

## Obsidian 插件

打开 [kgdistiller 社区页面](https://community.obsidian.md/plugins/kgdistiller)，点 **Add to Obsidian**，然后启用 **kgdistiller**。手动安装时，从 [plugin release](https://github.com/qiulinfan/kgdistiller/releases) 下载 `main.js`、`manifest.json`、`styles.css`，放进 `<vault>/.obsidian/plugins/kgdistiller/`，重载 Obsidian 后启用。需要桌面版 Obsidian **1.13.7 或更新**，移动端不加载此插件。

本 README 描述的是 0.1.5 及更新版本的插件。更早的版本读取旧的导出格式，看不到下文所说的任何记录。如果社区页面或 release 页面提供的仍是更早的版本，请改用 `kgd obsidian install` 安装，它会复制随包附带的 0.1.5。

记录存放在 vault 的隐藏目录 `.knowledge/` 里，Obsidian 默认会跳过它。打开 **Settings → kgdistiller → Index hidden knowledge folder** 之后，`.knowledge/` 会像普通文件夹一样出现在文件列表、搜索、Properties 和 Backlinks 中。后文的 `kgd obsidian install` 会从 Python 包安装插件，并顺带打开这个设置。

运行 **kgdistiller: Open typed graph**，图谱会在右侧栏打开。它画出 node、按 role 绑定的 relation、draft 和 pending term；可以只看当前 record、source 或 sheet 周围一到两层，也可以看全图，并按 kind、class、understanding 和 source 路径筛选。详情面板显示记录的 role、正文和 evidence，还有按钮打开记录本身、跳到 source 中被引用的那一行，或打开该 source 的 sheet。在 Obsidian 里改记录时视图会跟着更新。改记录用 Properties 面板，改完运行 `kgd check` 和 `kgd index`；插件本身从不写任何知识文件。完整说明见 [docs/obsidian.md](docs/obsidian.md)。

## 隐私与数据边界

插件从 Obsidian 的 metadata cache 读取 `.knowledge/entries/` 和 `.knowledge/drafts/` 的 frontmatter，选中某条记录时才读取该文件。它不发网络请求，不收集遥测，不需要账号，不读 vault 以外的文件，也不安装或更新任何东西。它唯一写的文件是自己的 `data.json`。为了索引隐藏目录，插件 patch 了 Obsidian 内部未公开的 file-adapter 方法，并用 Node 的文件系统 API 检查 vault 中 `.knowledge/` 下的路径，所以只支持桌面端。这样做也会让 `.knowledge/` 对 Obsidian 搜索和同一 vault 的其他插件可见。如果启用了 Hidden Folders Access 插件，kgdistiller 会报告冲突，并关闭自己的隐藏目录索引。

CLI 和 MCP server 都在本地运行。它们读取 home 中的 `config.json` 和 `types/`、各 base 的记录和 draft，以及已注册的 source 文件（用于核对 evidence 和 `kgd get --source-lines`）。写入范围只有：home（`kgd base add`/`rm` 改 `config.json`，第一次 `kgd base add` 创建的 `types/` 目录和 `.gitignore`，以及 `index.sqlite*`、`lock`）；base 的 `.knowledge/` 下的 `entries/`、`drafts/`、`sheets/`；`kgd obsidian install` 写 vault `.obsidian/` 中的插件目录、`community-plugins.json` 和插件的 `data.json`；`kgd claude link` 和 `kgd codex link` 写对应 agent runtime 的 home。MCP server 只走 stdio，没有写工具。

唯一的联网行为是 sentence-transformers 首次使用时把 embedding 模型下载到 Hugging Face 缓存（`BAAI/bge-m3` 有好几 GB）。不发送 token，也不运行远程代码。下载完成后设置 `HF_HUB_OFFLINE=1`，之后每次加载都只用本地文件。kgdistiller 不会替你 commit 或 push，每个 vault 和 home 请放在你自己的 Git 仓库里管理。

## 开源许可

kgdistiller 原创代码采用 [MIT](LICENSE)，版权 2026 Qiulin Fan，允许商业使用、修改和再分发，需保留版权和许可声明。插件 bundle 里包含 Cytoscape.js、cytoscape-fcose、cose-base 和 layout-base，均为 MIT；隐藏目录索引改编自 Hidden Folders Access 2.1.1（MIT）。完整声明已嵌入 bundle，并列在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 核心概念

kgdistiller 面向学术研究：读的论文、写的论文、自己的研究笔记。每个注册的目录（通常是一个 Obsidian vault）叫一个 base。base 把知识存成 Markdown 记录：正式记录在 `.knowledge/entries/`，提议中的记录在 `.knowledge/drafts/`，生成的审阅页在 `.knowledge/sheets/`。记录分两类：node（一个概念，或一条表述精确的结论），以及 relation（用 role 键绑定其他记录）。每条记录都引用本 base 里的一个 source 文件，写明路径、行号范围和原文引文。source 可以是任何格式的纯文本，kgdistiller 只把它当成带行号的文本来读，不解析语法。

全局 home 是 `$KGDISTILLER_HOME`（默认 `~/.knowledge`），负责注册 base、把 source glob 映射到你定义的 document type，并保存派生数据库 `index.sqlite`：

```text
~/.knowledge/                      # $KGDISTILLER_HOME
├── config.json                    # bases、source globs、embedding 模型
├── types/<name>.md                # 每个 document type 一个文件
├── .gitignore                     # "index.sqlite*" 和 "lock"
├── index.sqlite                   # 派生数据，由 `kgd index` 重建
└── lock

~/research/                        # 一个 base，通常是 Obsidian vault
├── notes/                         # sources：任意 UTF-8 文本
└── .knowledge/
    ├── entries/<id>.md            # 已接受的记录
    ├── drafts/<id>.md             # 提议中的记录，格式相同
    └── sheets/<source path>.md    # 生成的 def/pending sheet
```

数据库里有 FTS5 全文索引行和 float32 向量，向量用 NumPy 精确扫描。`kgd search` 用 reciprocal rank 融合三路：lexical、dense，以及对 label 和 alias 的名称匹配。如果上次 `kgd index` 之后记录文件有改动，每次读取都会在输出里报告 `lag`。知识本身是记录文件，数据库随时可以从它们重建。

## 安装

打包需要 Node 22 和 npm：插件的 `main.js` 从源码构建，不进版本库。所以直接 `uv tool install git+https://…` 会失败，要从 checkout 安装：

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
npm run build
uv tool install '.[retrieval]'
uv tool update-shell
```

这会在 Windows、macOS 和 Linux 上装好 `kgd` 和 `kgdistiller` 两个命令（同一个程序）。如果 `kgd` 还不在 `PATH` 上，重开一个 shell。`retrieval` extra 带来 dense 检索所需的 sentence-transformers 和 NumPy。不装它（`uv tool install .`）时，home 配置里保持 `"embedding": null`，搜索只用 lexical 和名称两路。

## 快速开始

注册一个 base。第一次 `base add` 会创建 home：

```sh
kgd base add ~/research --name research
```

在 `~/.knowledge/types/research-notes.md` 定义一个 document type。frontmatter 列出 node kind，以及每个 relation kind 的 role（有顺序）；正文是 agent 从这类文档里抽取记录时遵循的指引。kgdistiller 不自带任何 type。

```markdown
---
node_kinds: [definition, theorem]
relation_kinds:
  requires-for: [prerequisite, dependent]
  example: [uses, setting]
epistemic: [proved, stated]
---
Extract each definition and each precisely stated result as a node. Record a
relation only when the text states it. Unexplained terms become pending values.
```

在 `~/.knowledge/config.json` 里把 base 的 source 映射到这个 type，并选择 embedding 模型（不要 dense 检索就写 `null`）：

```json
{
  "bases": {
    "research": {
      "path": "~/research",
      "sources": {
        "notes/**/*.md": "research-notes"
      }
    }
  },
  "embedding": "BAAI/bge-m3"
}
```

然后添加记录。可以让装好 Skills 的 agent 记录一个定义、编译一整篇笔记；也可以在 `~/research/.knowledge/drafts/` 里手写 draft（格式见[记录格式](#记录格式)），再接受它：

```sh
cd ~/research
kgd sheet notes/measure.md --json
kgd check
kgd accept .knowledge/drafts/sigma-algebra.md
kgd index
kgd search "sigma algebra"
```

`kgd sheet --json` 打印该 source 的 type profile 和已有记录。`kgd check` 校验 home、记录和 draft，并报告位置移动了或与 source 对不上的 evidence。`kgd accept` 把 draft 移进 `entries/`，要么全部成功，要么什么都不改。`kgd index` 更新数据库；第一次 dense 运行会下载模型，之后请设置 `HF_HUB_OFFLINE=1`。所有命令都输出 JSON，退出码：成功 0，有问题或被拒绝 1，用法错误 2。

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
requires:
  - "set"
---
A sigma-algebra on a set X is a collection of subsets of X closed under complements and countable unions.

## Evidence

> A sigma-algebra on a set X is a collection of subsets of X that contains X
> and is closed under complements and countable unions.
```

一个 kind 为 `requires-for` 的 relation，role 来自 document type：

```markdown
---
label: Sigma-algebra is required for measure space
kind: requires-for
prerequisite:
  - "[[sigma-algebra]]"
dependent:
  - "[[measure-space]]"
epistemic: stated
source: notes/measure.md
lines: 6-7
---
A measure space is defined over a sigma-algebra.

## Evidence

> F is a sigma-algebra on X
```

- 必填：`label`、`kind`、`source`（相对 base 根目录的路径）和 `lines`（`a` 或 `a-b`）。可选：`aliases`、`understanding`（`unknown`、`not-yet-understood` 或 `understood`）、`epistemic`、`requires`。`tags` 和 `cssclasses` 允许出现，但会被忽略。
- 其余键都是 role。至少有一个非空 role 列表的记录是 relation，否则是 node。一条 relation 的 role 数量不限。
- 列表里的值要么是链接（`"[[id]]"`、`"[[base:id]]"` 或 `"[[.knowledge/entries/id]]"`），要么是纯文本 term。纯文本 term 就是 pending gap，表示 source 用到了这个词却没有解释，上面的 `set` 就是一例。
- 正文依次是说明文字、可选的 `## Search terms` 小节，最后是 `## Evidence` 小节，内容是从被引用行逐字摘出的引文。
- 例子和应用也是 relation，kind 由你注册，比如 `example: [uses, setting]`。

已接受的记录直接原地修改，改完运行 `kgd check` 和 `kgd index`。如果 source 被编辑导致引用行号偏移，`kgd check --fix-lines` 会对引文在 source 别处恰好只出现一次的记录重写 `lines:`。完整语法和校验规则见 [docs/model.md](docs/model.md)。

## 命令

| 命令 | 作用 |
|---|---|
| `kgd base add PATH [--name N]`、`base rm NAME`、`base list` | 注册、注销和列出 base，附带记录数、draft 数和索引数。 |
| `kgd check [--base B] [--fix-lines]` | 校验 home、记录和 draft，报告移动或过期的 evidence。 |
| `kgd sheet SOURCE [--json]` | 写出某个 source 的 sheet（记录、pending term、draft），或打印它的 profile。 |
| `kgd accept [--dry-run] DRAFT...` | 把 draft 转为正式记录，全部成功或全部不改。 |
| `kgd harvest [--dry-run] SHEET` | 接受 sheet 中勾选的 draft，并重新生成 sheet。 |
| `kgd index [--rebuild] [--no-embed]` | 让数据库与记录文件同步。 |
| `kgd search QUERY [--limit N] [--no-dense]` | 按 lexical、dense、名称三路给记录排序。 |
| `kgd resolve TERM...` | 列出 term 的各个义项、提及和 pending 用法。 |
| `kgd get UID... [--source-lines N]` | 读取完整记录及其链接，可附带 source 中当前的被引用行。 |
| `kgd neighbors UID... [--role R] [--dir out\|in\|both] [--depth N]` | 沿链接展开：依赖闭包和论断闭包。 |
| `kgd browse [HANDLE]` | 列出 base、某个 base 的 source 目录、某个 source 按 kind 分组的记录，或用 `--kind` 列出某类全部记录。 |
| `kgd pack UID... [--budget BYTES] [--requires-depth N]` | 在字节预算内打包完整记录，附带共享的 relation 和未补上的 gap。 |
| `kgd obsidian install [--base NAME]` | 在某个 base 的 vault 中安装或更新插件，并启用隐藏目录索引。 |
| `kgd claude link\|doctor`、`kgd codex link\|doctor` | 为 Claude Code 或 Codex 安装或检查 Skills、agent preset 和 workflow 文件。 |
| `kgd mcp` | 通过 stdio 为所有 base 提供只读工具。 |

`search`、`resolve`、`neighbors`、`browse` 和 `pack` 支持筛选参数 `--base`、`--kind`、`--class {node,relation}`、`--source PREFIX` 和 `--understanding`，都可以重复使用。id 在所有 base 中唯一时可以直接写 id，不带 base 前缀。各命令细节看 `kgd <command> --help`，输出格式见 [docs/retrieval.md](docs/retrieval.md)。

## Sheet 与 harvest

sheet 是单个 source 的审阅页。`kgd sheet notes/measure.md` 会写出 `.knowledge/sheets/notes/measure.md.md`，按 kind 列出该 source 的已接受记录、尚未解决的 pending term，以及以复选框形式列出的 draft：

```markdown
## Drafts
- [ ] [[.knowledge/drafts/measure-space|Measure space]] · definition · L6-7 — A measure space is a triple …
```

在 Obsidian 里打开 sheet，审阅或修改 draft，勾选要接受的那些。勾选只表示“接受这条 draft”，和你是否理解它无关。然后：

```sh
kgd harvest .knowledge/sheets/notes/measure.md.md
kgd index
```

harvest 一次性接受所有勾选的 draft，并重新生成 sheet。如果某条勾选的 relation 链接到一条没勾选的 draft，harvest 会拒绝执行、不做任何改动，并给出类似 `select [[measure-space]] too` 的提示。不要的 draft，直接删掉文件即可。

## Agent 集成

五个 Skill 覆盖写入和查询流程：

| Skill | 作用 |
|---|---|
| `capture-kgdistiller` | 边读边保存或更新一条记录（node、relation 或例子），带上引文和 pending term。 |
| `compile-knowledge-sheets` | 按 document type 从已注册 source 的一部分或全部抽取 draft，并写出 sheet。不会改动已接受的记录。 |
| `harvest-kgdistiller` | 对你勾选的 draft 运行 `kgd harvest`，再运行 `kgd index`。 |
| `query-kgdistiller` | 用 search、resolve、get、neighbors、browse 和 pack 从知识库作答，引用 `source:lines`。只读，配有 `kgdistiller-query-reviewer` agent preset。 |
| `deploy-kgdistiller` | 安装 kgdistiller，注册 base、source、document type 和模型，然后检查、建索引、安装插件、链接 runtime。 |

所有写知识的 Skill 最后都会运行 `kgd index`。按你用的 runtime 安装：

```sh
kgd claude link
kgd claude doctor
```

```sh
kgd codex link
kgd codex doctor
```

`link` 把 Skills、`kgdistiller-query-reviewer` preset 和 workflow manifest 装到 `~/.claude` 或 `~/.codex`（遵循 `CLAUDE_CONFIG_DIR` 和 `CODEX_HOME`），不会修改 `CLAUDE.md`、`settings.json`、`AGENTS.md` 或 `config.toml`。OpenCode 和 OMP 只装 Skills，在 checkout 里运行 `./scripts/link-skills.sh opencode` 或 `./scripts/link-skills.sh omp`（PowerShell 7：`./scripts/link-skills.ps1 -Runtime opencode`）。

MCP server 提供 `kg_search`、`kg_resolve`、`kg_get`、`kg_neighbors`、`kg_browse` 和 `kg_pack`，JSON 与 CLI 相同。模型在多次调用之间常驻内存，只有第一次 dense 搜索需要等加载。注册命令是 `kgd mcp`：

```sh
claude mcp add kgdistiller -s user -e HF_HUB_OFFLINE=1 -- kgd mcp
codex mcp add kgdistiller --env HF_HUB_OFFLINE=1 -- kgd mcp
```

OMP 原生从 `.mcp.json`、`.omp/mcp.json` 或 `~/.omp/agent/mcp.json` 加载 stdio MCP server，在 OMP 里执行 `/mcp add kgdistiller -- kgd mcp` 就会写入配置。使用 `omp -p` 时设置 `OMP_MCP_REQUIRE_READY=1`，让第一轮等 server 就绪。详见 [docs/deployment.md](docs/deployment.md#mcp-server)。

## 让 agent 安装

让 agent 从本仓库安装 kgdistiller 时，它应当：

1. clone 仓库，运行 `npm run build`、`uv tool install '.[retrieval]'` 和 `uv tool update-shell`，确认 `kgd --help` 能运行。
2. 链接自己的 runtime：先运行 `kgd claude link` 再运行 `kgd claude doctor`，或先 `kgd codex link` 再 `kgd codex doctor`；OpenCode 或 OMP 用 `./scripts/link-skills.sh opencode|omp`。完成后开一个新 session。
3. 按 `deploy-kgdistiller` Skill 操作：为每个 vault 运行 `kgd base add`，把 source glob、document type 和 `embedding` 写进 `~/.knowledge/config.json` 与 `~/.knowledge/types/`，再运行 `kgd check`、`kgd index`；在 Obsidian 中打开过的 vault 还要运行 `kgd obsidian install`。
4. 用 `kgd base list` 和 `kgd search "KNOWN NAME" --base NAME` 做冒烟测试。

之后，“把这个定义记进我的 kgd 知识库”或“在知识库里搜 measure space”这类请求就会交给对应的 Skill。

## 备份与恢复

每个 base 的 source 和 `.knowledge/entries/`、`drafts/`、`sheets/` 一起提交。home 的 `config.json`、`types/` 和 `.gitignore` 单独放一个私有仓库；`index.sqlite*` 和 `lock` 已被忽略。换新机器时，恢复这些文件，改好每个 base 的 `path`，然后重建：

```sh
kgd check
kgd index
```

数据库缺失或损坏时，`kgd index` 会从记录文件重建它，并重新 embed 所有记录。事先删除数据库（`rm -f ~/.knowledge/index.sqlite*`）不是必需的。在 Apple silicon 上用 `BAAI/bge-m3` 恢复一个 535 条记录的 base 用了 115 秒。`kgd index --rebuild` 重新生成所有行，但不重新 embed。

## 开发

```sh
npm run build
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv build --out-dir build/release/0.4.0
uv run --locked python scripts/check_distribution.py --dist-root build/release/0.4.0
npm test
(cd integrations/obsidian && npm ci && npm run check)
```

根目录的 `npm run build` 必须先跑：全新 checkout 里每个 `uv run` 和 `uv build` 都需要已构建的插件。测试使用临时 `KGDISTILLER_HOME` 和假 encoder。发布检查项（包括可选的真实模型测试）见 [docs/release.md](docs/release.md)。

延伸阅读：[docs/model.md](docs/model.md)（记录与写入流程）、[docs/retrieval.md](docs/retrieval.md)（数据库、搜索和 MCP 工具）、[docs/obsidian.md](docs/obsidian.md)（插件）、[docs/deployment.md](docs/deployment.md)（安装、配置、恢复）、[docs/product-workflows.md](docs/product-workflows.md)（Skills 与 workflow）。
