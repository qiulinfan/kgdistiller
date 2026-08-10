# Agentic 图谱知识库修复规范

Status: active execution plan

Baseline date: 2026-08-09

Current slice: `KB-REPAIR-03`

Repositories: 先修复 `kgdistiller`，再更新固定版本的 `qlblog` 宿主

## 1. 目的和规范层级

本文把
[`agentic-knowledge-base-closure-spec.md`](agentic-knowledge-base-closure-spec.md)
中的完整目标合同转换成严格顺序执行的修复计划。本文不重新定义 graph identity、
authority、provenance、transaction、retrieval 或 visibility 语义。

规范优先级为：

1. 用户的产品需求；
2. 闭环规范及其引用的数据合同；
3. 本修复规范；
4. 实现说明和 issue 描述。

发生冲突时使用更高层规范，并先修正本文再继续实现。只有所有 repair slice 完成，且
`C-PORTABLE`、`C-QUERY`、`C-ANNOTATED-INGEST`、`C-RAW-INGEST`、
`C-PAPER`、`C-HOST` 六条路径都有端到端证据，项目才可以宣称闭环。

## 2. 2026-08-09 审计基线

审计时 `kgdistiller` worktree 干净，位于 `8342239`；`qlblog` worktree 也干净，
但 submodule 仍固定在 `0406b8f`。

| Closure path | 基线 | 证据和阻塞缺口 |
| --- | --- | --- |
| `C-PORTABLE` | PARTIAL | store v1 能精确保存和恢复已有 vectors；没有 required coverage gate、embedding sync、retrieval readiness 和 qlblog 接线。 |
| `C-QUERY` | FAIL | exact、FTS、BFS、PPR 已公开；semantic lane 依赖 Python 注入 provider，正常 CLI/MCP 不可用。 |
| `C-ANNOTATED-INGEST` | FAIL | source patch 事务已完成；stable document matching、document upsert、embedding enrichment、portable refresh 和统一可续跑 receipt 未完成。 |
| `C-RAW-INGEST` | PARTIAL | 提取和 review 规则已有；Skill 不能继续完成 document upsert、embedding、portable readiness 和 materialization。 |
| `C-PAPER` | PARTIAL | 默认隔离论文流程已有规范；federated snapshot builder 和独立授权的 research import 未完成。 |
| `C-HOST` | FAIL | qlblog 使用 portable store 之前的引擎版本，没有 document/embedding 接线，也不能按 knowledge origin 过滤可视化。 |

新的 closure schemas 已经随包分发并通过合同测试，但 schema 存在不等于运行时能力已实现。

### 2.1 验证基线

- `uv build` 通过，wheel 和 sdist 均成功生成；
- Windows 完整单元测试运行 113 项，结果为 7 failures、32 errors；
- 本机缺少 Typst 能解释部分错误，但不能解释 Windows 上的 `fcntl`、`resource`、
  console encoding 和 deterministic glob validation 错误；
- CI 只覆盖 Ubuntu，因此当前不能宣称支持“任意机器”。

## 3. 所有修复都必须保持的原则

1. Markdown、Typst、LaTeX 始终是个人知识唯一可编辑的 authority。
2. Graph node identity 只来自显式 marker 和 reviewed identity decision。路径、标题、
   顺序、共现、embedding 和 similarity 都不能创建 identity。
3. Embedding 是非权威检索证据，也是需要精确保存的 portable asset；不能成为可信边或身份依据。
4. Query path 只读。一次 semantic query 最多创建一个 query-vector batch，绝不能隐式
   embed/re-embed document 或 node content。
5. 个人知识写入只使用一个高层事务边界。Skill 不得用低层 `sync`、`apply`、
   `reconcile` 重新拼装事务。
6. 已发布 schema 不改变既有语义。新增 required field 或 digest 规则必须发布新版本和显式迁移。
7. 失败阶段不能发布 partial portable generation，也不能宣称 `git_ready`。
8. 论文知识默认留在隔离 namespace；只有用户明确授权的 selected `new`/`partial`
   candidates 才能导入。
9. 引擎仓库不得加入个人知识、凭据、生成的个人图谱或 model key。
10. 用户可见说明、prompt 和 handoff 使用用户语言；命令、标识符、schema、action code
    和 raw error 保持原样。

## 4. 逐项执行协议

任何时刻只允许一个 repair slice 为 `IN PROGRESS`。当前项未达到完整 Definition of
Done 时，不开始后续项。

每项都按以下流程执行：

1. 将唯一一个 ledger 行标记为 `IN PROGRESS`；
2. 阅读对应实现和兼容性约束；
3. 完成该项最小的端到端切片；
4. 按风险加入正向、负向、失败恢复和幂等测试；
5. 运行本项验收命令和仓库级 gate；
6. 只在行为确实变化时更新文档、Skills、兼容性矩阵和 ledger；
7. 记录机器可验证的 receipt、artifact 或准确命令结果；
8. 没有剩余必需工作时才能标记 `COMPLETE`。

同一个 slice 不同时修改 `kgdistiller` 和 `qlblog`。宿主项只能在依赖的 engine commit
可以被 qlblog submodule 获取之后开始。

### 4.1 仓库级 gate

每个 kgdistiller 实现项必须运行：

```sh
uv run python -m unittest discover -s tests -v
uv build
git diff --check
```

每个 qlblog 宿主或 Skill 项必须遵守其 `AGENTS.md`；Skill 发生变化时运行 validator
和 linker，并至少运行：

```sh
make knowledge-workflow-check
make knowledge-check
make blog-check
make blog-build
git diff --check
```

平台相关项还必须运行声明的 OS matrix。缺少本地依赖必须与产品失败分开报告，不能通过
削弱或跳过 release tests 隐藏。

## 5. 修复项

### KB-REPAIR-00 — 恢复 Windows native + WSL 绿色基线

Repository: `kgdistiller`

Outcome: 在增加新能力前，让受支持的本地命令、事务、测试和 stress harness 在
Windows 原生环境和 Windows 主机上的 WSL Ubuntu 中都可运行。

Required work:

- 用一个小型 Windows/WSL lock abstraction 替换无条件 Unix `fcntl`，保持 non-blocking
  single-writer 语义；
- 让 stress resource measurement 在 Windows native 与 WSL 中都能运行，并如实报告
  RSS 或 unavailable metric；
- 让结构化 CLI JSON 在非 UTF-8 Windows console 安全输出，不改变写入 artifact 的
  canonical JSON bytes；
- 让 malformed registered glob 在两个必需环境上确定性拒绝；
- 所有 full-test CI job 安装 Typst；
- 在保留现有 Python 版本兼容性检查的同时增加 Windows CI；
- 记录 Windows native、WSL、Python 和 Typst 的支持矩阵。

Acceptance:

- 完整单元测试在 Windows native 和 WSL 全部通过；
- Windows 实际运行 lock/conflict/crash recovery、CLI Unicode 和 stress tests，不 skip；
- malformed glob 在 Windows native 和 WSL 返回相同稳定错误；
- wheel/sdist 构建成功；
- graph 和 schema 语义不改变。

### KB-REPAIR-01 — 激活 machine-local profile 和 provider registry

Repository: `kgdistiller`

Depends on: `KB-REPAIR-00`

Outcome: 每台机器记住 database、portable store 和 embedding profile，不提交凭据。

Required work:

- 从文档化的 machine-local 路径读取并验证 `qlkg-local-profile-v1`；
- 实现确定性优先级：显式 CLI option > local profile > repository default；
- profile 中相对路径按 profile 所在目录解析；
- 在现有 `EmbeddingProvider` protocol 周围建立 bounded adapter registry；
- 至少提供一个能真实 batch document/query embedding 的独立可测试 adapter；
- 凭据只从声明的环境变量读取，并从 error、status、receipt、log 中脱敏；
- 暴露不含秘密的 provider configuration digest。

Acceptance:

- 第二次 CLI 调用自动复用相同 database/store/profile；
- 显式 CLI option 确定性覆盖 profile；
- missing adapter/credential、dimension mismatch、timeout、invalid response 有稳定错误；
- fixture HTTP server 验证真实 batch request/response；
- 输出和 artifact 中不存在 credential value。

### KB-REPAIR-02 — 实现显式 embedding status/sync

Repository: `kgdistiller`

Depends on: `KB-REPAIR-01`

Outcome: embedding 只通过显式同步创建或更新，不再成为 query 的隐藏副作用。

Required work:

- 实现 `embedding status`、`embedding sync` CLI/Python API；
- 按 `qlkg-embedding-policy-v1` 计算 eligible active nodes；
- 将 vectors 分类为 ready、missing、stale、incompatible、unmanaged；
- 只把 missing/stale canonical inputs 分 batch 发给 provider；
- 安装结果前重新验证 graph generation，并原子写入；
- 逐字节保留 unchanged vectors；
- 限制 batch size、retry、response size 和总工作量；
- 按 profile/node type 报告 coverage。

Acceptance:

- 第一次 sync 只 embed eligible missing/stale nodes；
- 第二次 sync 的 document embedding call count 为 0；
- 修改一个 node 只失效其 canonical input 和对应 vectors；
- provider 工作期间 graph 改变时拒绝 stale vector batch；
- provider 失败不损坏已有 ready vectors 或 graph。

### KB-REPAIR-03 — 执行公开的 planned hybrid retrieval

Repository: `kgdistiller`

Depends on: `KB-REPAIR-02`

Outcome: CLI、Python、MCP 从一个 bounded `qlkg-retrieval-plan-v1` 执行 keyword、graph、
vector retrieval，并返回 `qlkg-search-result-v2`。

Required work:

- 分别验证并执行 identity、lexical、semantic、graph expressions；
- semantic lane 只使用已经 materialized 的 node vectors；
- 每个 semantic query 最多发出一个 batched query-vector request；
- 删除当前 `semantic_search -> index_embeddings` 副作用；
- 确定性融合各 lane，并保留 per-lane evidence；
- 每个 lane 都报告 enabled/disabled/degraded 和 reason code；
- 通过 CLI/read-only MCP 暴露 plan execution，MCP 参数不接收 secret；
- 保持保守 identity 规则和 bounded context。

Acceptance:

- 三路 fixture 同时返回 lexical、semantic、graph evidence；
- 查询期间 document/node embedding call count 为 0；
- semantic disabled 时 query-vector call 为 0，enabled 时最多一个 batch；
- provider/vector 缺失时显式 degraded，不静默丢 lane；
- semantic score 永远不能消除 ambiguous identity。

### KB-REPAIR-04 — 用 retrieval readiness 管控 portable generation

Repository: `kgdistiller`

Depends on: `KB-REPAIR-02`, `KB-REPAIR-03`

Outcome: store integrity、embedding coverage、materialization、semantic readiness 成为
相互区分的机器可读状态。

Required work:

- 将 store generation 绑定 document v2 inventory、embedding policy、required profile
  configuration 和 coverage；
- 实现 ready、partial、unmanaged portable states；
- required coverage 不足时普通 snapshot 不安装新的 manifest/inventory；
- 显式 `--allow-partial` 可以生成 partial，但不能宣称 retrieval-ready；
- `store verify/materialize` 和 deploy status 使用一致状态词汇；
- 保持 manifest-last 和 last-known-good recovery。

Acceptance:

- coverage gate 失败时上一份 generation bytes 不变；
- threshold、partial override、unmanaged store 都有 fixture；
- 恢复后的 vectors 无 document re-embedding 即可通过真实 semantic-query smoke test；
- integrity-valid 不会被误报成 semantic-search-ready。

### KB-REPAIR-05 — 实现 stable document inventory v2

Repository: `kgdistiller`

Depends on: `KB-REPAIR-00`

Outcome: 文档更新和 reviewed move 维持一个 inventory identity，不影响 graph node identity。

Required work:

- 兼容读取 document v1，新 generation 写 document v2；
- matching precedence：explicit `document_id`、normalized external ID、current/history
  authority、exact content digest；
- 多个匹配必须 ambiguous；
- unmatched new document 创建 stable ID；
- content-changing move 必须提供原 document ID；
- authority history bounded、deduplicated；
- 提供显式 v1-to-v2 migration。

Acceptance:

- Markdown/Typst/LaTeX 分别覆盖 create、exact no-op、update、reviewed move、ambiguous；
- 同 document ID/hash 重复 apply 不改变 generation；
- move 不产生第二个 document 或重复 graph identity；
- 永远不使用 fuzzy text similarity 匹配。

### KB-REPAIR-06 — 实现 annotated document plan/apply

Repository: `kgdistiller`

Depends on: `KB-REPAIR-05`

Outcome: fully reviewed/marked document 通过一个高层 upsert API 预测并提交 authority/graph stage。

Required work:

- 增加 `ingest document plan/apply` 和对应 Python API，消费
  `qlkg-document-upsert-request-v1`；
- 绑定 document match、source ownership、candidate/query/delta digests 及
  graph/source/document/portable preconditions；
- 内部只转换成一次现有 low-level transaction；
- 预测 operation、marker/ref/entry/edge、vector invalidation 和后续 readiness；
- 产生 `qlkg-document-ingest-receipt-v1` 的 authority/graph stage；
- exact no-op 幂等且 byte-preserving。

Acceptance:

- stale graph/source/query/document/portable precondition 全部零写入拒绝；
- plan 只读，apply 可 crash recovery；
- create/update/move/no-op receipt 都指向一个 stable document；
- Skills 不再需要调用低层写命令。

### KB-REPAIR-07 — 完成 resumable enrichment 和 materialization

Repository: `kgdistiller`

Depends on: `KB-REPAIR-04`, `KB-REPAIR-06`

Outcome: document apply 继续完成 embeddings、portable publication、local materialization，
并使用一个真实、可续跑 receipt。

Required work:

- 编排 authority graph、embeddings、portable、materialization 四个 receipt stages；
- 每个后续 stage 绑定 committed graph/document generation；
- 相同 canonical request 从第一个 incomplete stage 恢复；
- graph committed/provider failed 表示 degraded，不谎称 rolled back；
- 新 generation 完整验证前保持上一份 portable generation active；
- 只有所有 required stages complete/current 时设置 `overall_status=ready`、
  `git_ready=true`；
- deploy/ingest Skill 不得把 pending/degraded 说成跨机器完成。

Acceptance:

- provider 失败返回 graph committed + embedding pending/degraded；
- retry 不重写 authority/graph，只继续 enrichment；
- embedding/materialization 期间 stale generation 可检测；
- ready receipt、store manifest、SQLite metadata、status digests 完全一致。

### KB-REPAIR-08 — 更新 canonical engine Skills

Repository: `kgdistiller`

Depends on: `KB-REPAIR-03`, `KB-REPAIR-07`

Outcome: query、ingest、deploy Skills 只使用已完成的 public interfaces，并如实报告 degraded state。

Required work:

- `query-kgdistiller` 在 batch identity resolve 后生成 lane-specific retrieval plan；
- `ingest-kgdistiller` 只使用 document plan/apply，只有 ready receipt 才宣称完整入库；
- `deploy-kgdistiller` 区分 integrity、coverage、materialization、semantic readiness、Git state；
- 继续禁止 raw graph/SQLite read、identity inference、credential 泄漏和未授权 Git 操作；
- 更新 English discovery metadata，并加入 maintained personal Skill 的用户语言对齐规则。

Acceptance:

- 三个 Skills 通过 active validator；
- isolated Agent behavior tests 证明 lane planning、write boundary、ambiguity preservation、
  no secret/raw-graph access；
- Skill claims 与 receipt/status evidence 完全一致。

### KB-REPAIR-09 — 将完整引擎接入 qlblog

Repository: `qlblog`

Depends on: 包含 `KB-REPAIR-08` 且可获取的 engine commit

Outcome: qlblog 固定使用支持 profile、hybrid query、document upsert、embedding、portable
readiness 和 canonical Skills 的一个引擎版本。

Required work:

- 更新 kgdistiller submodule；
- 更新 thin discovery entries、Skill catalog、workflow docs、Make targets、compatibility tests；
- personal-note create/update/no-op 全部接 document upsert；
- 为 Markdown/Typst/LaTeX 增加 host fixtures；
- production portable store 默认由用户选择且保持 private；未经明确授权，不把 embeddings
  或 research source 提交到公开 qlblog；
- Skill 变化后按 qlblog 协议立即运行 linker。

Acceptance:

- clean clone 完成 create/update/no-op 和 hybrid query；
- qlblog receipt 记录实际 pinned engine commit；
- 公开仓库不含 credential、private store 或 hidden research authority；
- knowledge/site checks 全部通过。

### KB-REPAIR-10 — 产品化 federated paper snapshot

Repository: `kgdistiller`

Depends on: `KB-REPAIR-03`

Outcome: 默认论文流程生成 deterministic、validated、read-only federated artifact，
不再手工拼 envelope。

Required work:

- 发布 versioned federated-paper snapshot schema 和 deterministic builder/validator；
- 绑定 paper candidate snapshot、personal target digests、comparison report、exact reviewed
  bridges、dossiers、learning route；
- known 只保留 role + exact bridge，partial 只保留 missing material，new 使用 source-backed dossier；
- conflict/uncertain 不 bridge，且可独立寻址审查；
- 构建前后 personal graph/snapshot/alignment bytes 不变；
- new/partial dossier records 使用 stable IDs。

Acceptance:

- 重复构建 bytes/digests 相同；
- dangling bridge、stale target、missing source location、duplicated known entry 均验证失败；
- 完整 synthetic 或真实 paper package 通过默认流程，personal digests 不变。

### KB-REPAIR-11 — 增加明确授权的 paper knowledge import

Repository: `qlblog`

Depends on: 可获取的 `KB-REPAIR-09`、`KB-REPAIR-10` commits

Outcome: 独立 `import-paper-knowledge` Skill 只导入用户选中的 `new`/`partial` candidates。

Required work:

- 只接受 validated paper package/federated snapshot、exact personal digests、selected IDs、
  reviewed provenance 和 destination；
- 创建独立 research authority，记录 title、authors、version、DOI/arXiv/URL、BibTeX 及
  page/section/equation/figure/table locations；
- known 写 native refs；只有 selected new/partial 写 marker、entry、evidence-backed edge；
- research source 默认 `knowledge_origin: research`、`publish: false`、`listed: false`；
- 调用 annotated document upsert 并要求 ready receipt；
- 默认 paper workflow 永远不构成 import authorization。

Acceptance:

- unselected、known、conflict、uncertain 不创建新 personal identity；
- 每个 imported node/edge 都能回到 research source location；
- 缺少授权时 registry、personal graph、store、site 全部不变；
- Skill validator/catalog/workflow/linker checks 全部通过。

### KB-REPAIR-12 — 增加一致的 research visibility controls

Repository: `qlblog`

Depends on: `KB-REPAIR-11`

Outcome: public projection 继续以 source 为硬门禁；允许发布的 research 可以按 origin 一致过滤。

Required work:

- 保留 source `publish` 作为 public-data security boundary；
- 增加 `all`、`personal-note`、`research` filters；
- origin filter 在计算 nodes、edges、references、search results、neighborhoods、statistics
  之前生效；
- 隐藏 research 必须移除 research-only data，不能只改 CSS visibility；
- circle/square 只作为视觉表达，不是安全边界。

Acceptance:

- unpublished research 不进入 public graph payload；
- research hidden 时不存在 research-only ID、edge、ref、result、detail、count 泄漏；
- all/personal/research API/UI fixtures 通过 site checks 和 production build。

### KB-REPAIR-13 — 记录闭环和 release evidence

Repositories: 先 `kgdistiller`，再在独立 run/commit 中处理 `qlblog`

Depends on: `KB-REPAIR-00` 至 `KB-REPAIR-12`

Outcome: 所有产品声明都有 fresh-clone、cross-platform、end-to-end 证据。

Required evidence:

- 另一台 clean machine/environment 完成 portable snapshot/verify/materialize + semantic query；
- hybrid retrieval 的 lane 和 provider call-count evidence；
- 三种 authority 的 document create/update/move/no-op 完整矩阵；
- raw personal note 从提取到 ready receipt 和 site build；
- 默认 paper federation 保持 personal digests 不变；
- 明确授权的 selected paper import 带完整 provenance；
- research visibility projection/UI checks；
- cross-platform tests、package build、Skill validation、qlblog checks、fresh submodule install；
- README、deployment、release、compatibility、catalog、workflow 文档不夸大 partial behavior。

Acceptance:

- 六条 closure paths 在本文和 closure ledger 中全部为 `PASS`；
- evidence 不含 personal authority body、credential、model key 或 generated private graph；
- 只有此时 README/Skills 才能宣称 portable hybrid RAG、document upsert、raw-document
  ingestion 和 authorized paper import 已闭环。

## 6. Execution ledger

允许状态：`NOT STARTED`、`IN PROGRESS`、`BLOCKED`、`COMPLETE`。

| Slice | Status | Completion evidence |
| --- | --- | --- |
| `KB-REPAIR-00` | COMPLETE | Commit `1cb7f31`、PR #1；本地 Windows 3.9/3.11/3.13 与 WSL 3.14 全量 178 项通过；build、distribution/wheel smoke 通过；GitHub Actions [run 31418772924](https://github.com/qiulinfan/kgdistiller/actions/runs/31418772924) 的 Linux 3.9/3.11/3.13、Windows、macOS 五项全部通过。 |
| `KB-REPAIR-01` | COMPLETE | Commit `0fdf6be`、PR #2；本地 Windows Python 3.9/3.13 与 WSL Python 3.14 全量 194 项通过，Python 3.9/3.11/3.13 定向验收通过，build、distribution/wheel smoke 与独立安全/acceptance review 通过；GitHub Actions [run 31427799672](https://github.com/qiulinfan/kgdistiller/actions/runs/31427799672) 的 Linux 3.9/3.11/3.13、Windows、macOS 五项全部通过。 |
| `KB-REPAIR-02` | COMPLETE | Commit `42f92b6`、PR #3；本地 Windows Python 3.9/3.13 与 WSL Python 3.14 全量 227 项通过，build、distribution/wheel smoke 与三路独立 security/acceptance/diff review 通过；GitHub Actions [run 31440537213](https://github.com/qiulinfan/kgdistiller/actions/runs/31440537213) 的 Linux 3.9/3.11/3.13、Windows、macOS 五项全部通过。 |
| `KB-REPAIR-03` | IN PROGRESS | 执行公开的 bounded hybrid retrieval，并验证 query-only、lane degradation、budget 和 explainable fusion。 |
| `KB-REPAIR-04` | NOT STARTED | — |
| `KB-REPAIR-05` | NOT STARTED | — |
| `KB-REPAIR-06` | NOT STARTED | — |
| `KB-REPAIR-07` | NOT STARTED | — |
| `KB-REPAIR-08` | NOT STARTED | — |
| `KB-REPAIR-09` | NOT STARTED | — |
| `KB-REPAIR-10` | NOT STARTED | — |
| `KB-REPAIR-11` | NOT STARTED | — |
| `KB-REPAIR-12` | NOT STARTED | — |
| `KB-REPAIR-13` | NOT STARTED | — |

当前只执行 `KB-REPAIR-03`；在它通过独立验收前不开始后续 repair slice。
