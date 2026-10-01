<div align="center">

<h1>kgdistiller</h1>
<p><strong>从笔记与研究材料构建有来源的知识图谱。</strong></p>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Obsidian 1.13.7+](https://img.shields.io/badge/Obsidian-1.13.7%2B-7c3aed)](manifest.json)
[![Plugin release](https://img.shields.io/github/v/release/qiulinfan/kgdistiller?label=Obsidian%20plugin)](https://github.com/qiulinfan/kgdistiller/releases)

[English](README.md) · **简体中文**

</div>

## 主要功能

- 从 Markdown、Typst、LaTeX 中的显式标记建立概念身份。
- 在 Obsidian 或本地浏览器中查看有方向、有类型、有证据的概念关系。
- 分别展示来源定义、来源引用和经过审阅的语义关系。
- 使用 CLI 与有明确边界的 Agent 工作流查询、整理、导入和导出知识。

本仓库同时维护 **Obsidian 插件 0.1.0** 和 **Python 核心 0.4.0**。
笔记中的显式身份标记与原始内容保留在你的知识仓库；图谱、阅读页面和
Obsidian 投影是可重新生成的产物。

## 安装 Obsidian 插件

打开 [kgdistiller 社区页面](https://community.obsidian.md/plugins/kgdistiller)，点击 **Add to Obsidian**，然后启用 **kgdistiller**。

手动安装时，从 [GitHub Release](https://github.com/qiulinfan/kgdistiller/releases) 下载
`main.js`、`manifest.json`、`styles.css`，放入
`<vault>/.obsidian/plugins/kgdistiller/`，重载 Obsidian 并启用 **kgdistiller**。
最低版本为 **1.13.7**。

插件读取 vault 内的 `kgdistiller-obsidian-graph-v1` 投影。已安装 CLI、注册
知识仓库的用户可以生成投影：

```sh
kgdistiller --vault research export obsidian --replace
```

执行 **kgdistiller: Open typed graph** 即可打开图谱。默认路径是
`knowledge/build/obsidian/semantic-graph.json`，可在设置中修改。图中可以筛选
关系和领域，查看边的证据，并打开概念笔记或来源笔记。

插件运行时不需要 Python 或服务进程；**生成和刷新投影需要单独安装
kgdistiller CLI（Python ≥3.9）**。移动端可以读取在其他设备生成并同步到
vault 的投影。CLI 不在插件内部运行，也不会自动安装或更新。

## 核心快速开始

安装全局命令：

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
uv tool update-shell
kgdistiller --help
```

在笔记仓库内初始化、检查来源注册表，再生成第一版图谱：

```sh
kgdistiller init --source-root notes
kgdistiller sync
kgdistiller check
kgdistiller vault register /absolute/path/to/your-notes-repository --name research
```

### 显式身份标记

| 格式 | 定义 | 引用 |
| --- | --- | --- |
| Markdown | `--[[Measure space]]--` | `[[Measure space]]` |
| Typst | `#kn[Measure space]` | `#ref[Measure space]` |
| LaTeX | `\kn{Measure space}` | `\knref{Measure space}` |

标题、文档顺序和词语共现不建立概念身份。名称变更、别名和语义关系经过
审阅后再写入；查询与图谱展示保持来源绑定。

```sh
kgdistiller --vault research agent status
kgdistiller --vault research agent resolve "Measure space"
kgdistiller --vault research agent search "measure space" --limit 20
kgdistiller --vault research agent get measure-space
```

Codex 集成使用 `kgdistiller codex link` / `kgdistiller codex doctor`；
Claude Code 集成使用 `kgdistiller claude link` / `kgdistiller claude doctor`。
普通论文阅读不触发导入；独立论文工作流和事务导入要求详见
[完整核心说明](README.md#core-reference) 与 [工作流指南](docs/product-workflows.md)。

## 数据与隐私

Obsidian 插件只通过 vault API 读取选定投影和打开笔记，不发送远程请求、
不收集遥测、不访问 vault 外文件、不自动安装或更新插件及依赖。
投影由你手动运行外部命令刷新，成功修改产物后，打开的图谱视图自动重载。

核心构图和查询在本地进行，无需帐号或网络服务。MCP 和本地浏览器是独立
的可选入口；浏览器默认监听 `127.0.0.1`。研究 Agent 和可选外部工具使用
你自行配置的服务。私人知识来源、图谱及凭据保留在你的知识仓库中。

## 开源许可

kgdistiller 原创代码采用 [MIT](LICENSE)，版权为 2026 Qiulin Fan；
允许商业使用、修改和再分发，须保留版权与许可声明。第三方组件保留原许可证。
Cytoscape.js 的完整 MIT 通知已嵌入实际插件产物，见
[第三方通知](THIRD_PARTY_NOTICES.md)。

[插件说明](integrations/obsidian/README.md) · [发布与合同边界](docs/release.md) ·
[完整英文参考](README.md)
