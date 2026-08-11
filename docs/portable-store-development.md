# Portable knowledge store：需求、方案比较与第一版实现

## 1. 背景

kgdistiller 是一个面向个人生产环境的小型知识库系统。它以 Markdown、
Typst 和 LaTeX 文件作为知识来源，通过显式的知识标记构造知识图谱，并
结合全文、图遍历、PPR 和 embedding 完成混合召回。

这个系统已经嵌入日常工作流，包括但不限于：

- 笔记导出和知识标记同步；
- 论文阅读、概念抽取和候选图谱比较；
- Agent 查询、上下文打包和经过 review 的事务入库；
- 本地浏览、MCP 查询和下游发布。

原有实现对单机使用已经足够，但“知识库究竟由哪些文件组成、应该备份
什么、另一台机器如何恢复”并不直观。SQLite 同时承载查询索引和
embedding 缓存，也容易让用户误以为数据库文件本身就是知识库。

## 2. 用户需求

本次迭代来自以下连续需求。

### 2.1 单机损坏后的恢复

知识库只存在于一台机器时，磁盘或整机损坏可能同时丢失：

- 已经入库的 Markdown、Typst 和 LaTeX 文件；
- 当前知识图谱及其 reviewed identity/alignment 信息；
- 已经付费或耗时生成的 embedding；
- “哪些文档已经进入当前 generation”这一事实。

只备份 SQLite 不足以解决问题，因为 SQLite 是查询实现，不应成为知识
身份和语义关系的权威来源。只备份原始文件也不够，因为 reviewed 图谱
数据和 exact embedding 仍可能丢失。

### 2.2 多机器之间的知识不可见

用户可能在多台机器上工作。如果入库 Skill 只修改当前机器的本地文件和
SQLite，其他机器不会自动知道：

- 新增或修改了哪些知识源；
- 图谱 generation 是否已经变化；
- 哪些 embedding 仍然有效；
- 是否需要重新构建本机数据库。

期望的操作模型是：一台机器完成 review 和入库后，产生一个可提交的完整
generation；其他机器通过 `git clone` 或 `git pull` 获得该 generation，
再快速物化本地数据库。

### 2.3 降低部署和访问门槛

用户不应该先理解 SQLite 表、内部 entry shards 或索引生命周期，才能部署
kgdistiller。系统需要一个面向用户的部署 Skill 和一组高层命令，明确：

- Git 应追踪哪些文件；
- 哪些内容只属于本机；
- 如何创建、验证、更新和恢复知识库；
- 当前状态只是本地生成、本地 commit，还是已经同步到远端。

### 2.4 持久化 exact embedding

embedding 具有以下特点：

- provider 调用可能收费或耗时；
- 模型可能下线、重命名或变更实现；
- 相同模型名在未来不一定给出逐字节一致的结果；
- 多机器重新计算会产生不必要的成本和漂移。

因此，虽然 embedding 不能定义图谱身份或可信语义关系，其 exact vector
bytes 仍然是值得分布式保存的检索产物。第一版必须支持在另一台机器上
恢复 node embeddings，而不是强制重新计算全部文档向量。

## 3. 目标与非目标

### 3.1 目标

第一版需要满足：

1. 以一个普通目录作为可 Git 管理的 portable knowledge store。
2. Store 包含已经入库的源文件、来源清单、确定性图谱、reviewed registries
   和 exact embeddings。
3. Store 使用版本化 JSON/JSONL 和内容寻址二进制对象，不使用 SQL 分片。
4. SQLite 继续作为每台机器本地生成的查询索引，默认不进入 Git。
5. 新机器在没有文档重新 embedding 的情况下恢复 SQLite 中的 exact vectors。
6. 所有 source、manifest、record 和 vector object 都经过 digest 验证。
7. deterministic core 保持 provider-neutral；snapshot 和 materialize 不调用
   embedding provider。
8. 保持 Markdown、Typst 和 LaTeX 三种 authority 格式兼容。
9. 保持原有图谱原则：只有显式 marker 定义知识身份；embedding 永远不能
   合并节点或产生可信边。
10. 提供一个部署 Skill，覆盖创建、Git 边界、同步、恢复和状态报告。

### 3.2 非目标

第一版不试图提供：

- 多用户数据库服务或权限系统；
- 类似 Dropbox 的实时文件同步；
- 自动解决两台机器上的 Git 语义冲突；
- 自动创建 Git remote、commit 或 push；
- 将 embedding similarity 直接升级为 identity 或 graph authority；
- 保存查询向量、查询日志、provider credentials 或 API keys；
- 为大规模向量库实现 ANN 服务、对象存储或分布式事务；
- 复制 Markdown、Typst、LaTeX 之外的任意资源树。

## 4. 方案比较

### 4.1 跨机器同步格式

| 方案 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- |
| 直接提交 SQLite | clone 后可立即查询；单文件 | Git diff/merge 不透明；WAL/版本/平台行为增加风险；容易把查询实现误当权威数据 | 不采用 |
| SQL dump 或 SQL 分片 | 文本可读；可重建数据库 | 顺序、转义、schema migration 和冲突噪声大；仍然暴露内部查询模型 | 不采用 |
| 单个大型 JSON | 结构明确；容易校验 | 任意小改动都会重写大文件；合并冲突集中 | 只用于小型 manifest |
| 版本化 JSON + JSONL | diff 友好；记录可排序；schema 可版本化；易做 canonical digest | 需要本地 materialize 步骤 | 采用 |
| 事件日志 | 可表达历史和增量 | replay、compaction、幂等和迁移复杂；个人小型知识库收益不足 | 暂不采用 |
| 云数据库作为中心 | 多机实时一致 | 引入服务、费用、鉴权、网络和 provider lock-in；违背 local-first | 不作为默认方案 |

结论：Git 中保存版本化、确定性的 JSON/JSONL authority generation；SQLite
只保留为本地 materialized query view。不要将 SQL 分片作为同步协议。

### 4.2 embedding 是否进入 portable store

| 方案 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- |
| 完全不保存，按需重算 | Store 最小；实现简单 | 成本、耗时和模型漂移；模型不可用时无法恢复 | 不满足需求 |
| 随 SQLite 一起提交 | 恢复直接 | 继承 SQLite 的 Git 和迁移问题；向量与查询 schema 耦合 | 不采用 |
| Base64 放入一个 JSONL | 单一文本协议 | 体积膨胀约三分之一；同一向量重复；一行可能很大 | 不采用 |
| 每个向量一个内容寻址 `.f32` 对象 | exact bytes；天然去重；单文件有界；记录和对象解耦 | 小文件数量随节点增长 | 第一版采用 |
| 多向量二进制 shard | 文件数量少；顺序读取快 | 小改动重写 shard；索引和 compaction 更复杂 | 作为未来扩展 |
| Git LFS | Git 仓库本体较小 | 恢复依赖第二套对象服务；离线 clone 可能只拿到 pointer | 小型个人库不默认采用 |

结论：保存 exact node embeddings。每个 vector 作为 little-endian float32
内容寻址对象；JSONL 记录负责绑定 node、provider、model、dimensions、
canonical input digest 和 vector digest。

### 4.3 Store 放在哪里

| 布局 | 适用场景 | 权衡 |
| --- | --- | --- |
| 笔记仓库原地作为 store | 笔记仓库已经私有并且就是希望同步的单元 | 路径最简单；source 和 store 同步提交 |
| 独立 private repository | 原始笔记分散、现有仓库不适合提交向量，或希望单独备份 | 初次需要 `--output` 复制；后续应明确哪个副本是 primary |

两种布局都支持。独立输出目录不得嵌套在源项目内，也不得包含源项目，
以避免递归复制和不清晰的所有权。

## 5. 最终架构

系统被划分为两个层次。

### 5.1 Portable authority store

这是 Git、备份和跨机器同步的单元。它回答：

- 当前 generation 包含哪些 authority 文件；
- 图谱、identity 和 alignment 的精确版本是什么；
- 哪些 node embeddings 被保存；
- 所有文件是否仍然组成同一个完整 generation。

推荐布局：

```text
personal-knowledge-store/
├── notes/                                  # 已入库的 md/typ/tex authority
├── knowledge/
│   ├── .gitignore                          # 默认忽略 build/
│   ├── sources.json                        # qlkg-sources-v2
│   ├── identities.json                     # optional qlkg-identities-v1
│   ├── alignments.json                     # qlkg-alignments-v1
│   ├── graph/                              # qlkg-v2 + entry shards
│   ├── documents.jsonl                     # qlkg-document-record-v2
│   ├── embedding-policy.json               # qlkg-embedding-policy-v1
│   ├── store.json                          # qlkg-store-v2
│   └── embeddings/
│       ├── manifest.json                   # qlkg-embedding-bundle-v2
│       ├── records.jsonl                   # qlkg-embedding-record-v2
│       └── objects/
│           └── ab/
│               └── ab...cd.f32             # SHA-256 content-addressed bytes
└── vendor/kgdistiller/                     # optional pinned engine
```

### 5.2 Local materialized index

`knowledge/build/knowledge.sqlite` 只属于当前机器。它包含 FTS、normalized
names、typed edges、refs、alignment mappings、embeddings 和查询元信息。

SQLite 可以被删除和重建。其 schema 不构成跨机器协议，也不定义节点身份。
当 SQLite 中记录的 `store_generation_sha256` 与 store 相同时，materialize
可以直接返回 no-op。

## 6. 数据契约

### 6.1 `qlkg-store-v2` and v1 compatibility

`knowledge/store.json` 是最后写入的顶层 manifest，主要字段包括：

```json
{
  "schema": "qlkg-store-v2",
  "generator": "kgdistiller",
  "paths": {
    "registry": "knowledge/sources.json",
    "identities": "knowledge/identities.json",
    "alignments": "knowledge/alignments.json",
    "graph": "knowledge/graph",
    "documents": "knowledge/documents.jsonl",
    "embedding_policy": "knowledge/embedding-policy.json",
    "embedding_manifest": "knowledge/embeddings/manifest.json"
  },
  "documents": {
    "record_schema": "qlkg-document-record-v2",
    "count": 1,
    "sha256": "...",
    "source_snapshot_sha256": "...",
    "document_generation_sha256": "..."
  },
  "readiness": {
    "state": "ready",
    "profiles": [{"name": "primary", "readiness": "ready"}]
  },
  "embedding_policy_file_sha256": "...",
  "embedding_policy_sha256": "...",
  "readiness_sha256": "...",
  "registry_sha256": "...",
  "identity_sha256": "...",
  "alignment_sha256": "...",
  "graph_sha256": "...",
  "knowledge_generation_sha256": "...",
  "embedding_generation_sha256": "...",
  "store_generation_sha256": "...",
  "store_sha256": "..."
}
```

Generation digest 含义如下：

- `knowledge_generation_sha256`：绑定 registry、source inventory、graph、
  identities 和 alignments；
- `embedding_generation_sha256`：绑定 embedding records、objects 和 provider
  configurations；
- `store_generation_sha256`：绑定 knowledge、document、embedding、policy 和
  readiness generation，作为本机 materialize 的幂等键。

`store_sha256` 对 manifest 除自身之外的 canonical JSON 计算 SHA-256。v1
manifest 继续可读和 materialize，但没有 policy、required configuration 或
coverage binding，因此 verify 必须合成 `unmanaged` / `retrieval-not-ready`，不能
根据 vector 数量推断 readiness。

### 6.2 `qlkg-document-record-v2` and v1 compatibility

`knowledge/documents.jsonl` 每行描述一个进入当前图谱 generation 的文件：

- stable `document_id`、`source_id` 和 `knowledge_origin`；
- repo-relative `authority`；
- reviewed `authority_history` 和 normalized external IDs；
- `md`、`typ` 或 `tex` 格式；
- exact `source_sha256`；
- 该文件当前拥有的 active definition IDs；
- reference count。

Inventory 必须与 `qlkg-v2` manifest 的 `source_hashes` 完全相等。多余、缺失
或 hash 不一致的 authority 都会导致 verify 失败。已发布 v1 records 继续可读，
但 writer 不会向 v1 增加 required identity 字段或伪装成 v2。

### 6.3 `qlkg-embedding-bundle-v2`

Embedding manifest 记录：

- `embedding_input_schema: qlkg-node-embedding-text-v1`；
- `dtype: float32-le`；
- records 的路径、数量和 SHA-256；
- distinct object 数量、总字节数和 object-set digest；
- provider/model/dimensions/config digest inventory；
- `embedding_generation_sha256`。

Snapshot 只写 v2 bundle。Verifier 和 materializer 继续读取已发布的
`qlkg-embedding-bundle-v1`，但不会把 v1 原地伪装成 v2。

### 6.4 `qlkg-embedding-record-v2` 与 v1 compatibility

当前 v2 embedding record 的逻辑主键是：

```text
(namespace, node_id, provider, model)
```

每条记录绑定：

```text
namespace
node_id
provider
model
dimensions
embedding_input_schema
content_sha256
provider_config_sha256
vector_sha256
```

`content_sha256` 来自 deterministic canonical embedding input，而不是文件
位置或 chunk 顺序。`provider_config_sha256` 是 KB01 对本机 non-secret
vector-space configuration 计算的 opaque SHA-256；portable store 不携带
base URL、credential environment name 或 secret。V2 export/import 必须逐行
精确保留这个 digest。配置切换会替换同一逻辑主键的旧向量；portable bundle
不能包含同一 `(namespace, node_id, provider, model)` 逻辑主键的两个配置版本。

已发布的 `qlkg-embedding-record-v1` 仍以
`(namespace, node_id, provider, model)` 四元组为逻辑主键。V1 digest 只能按其
旧 portable provider configuration（provider、model、dimensions、dtype、
input schema）重算并验证；错误 digest 必须拒绝。Materialize v1 时保留该
legacy digest，不能替换为当前机器的配置 digest。

只有 node input、provider、model、dimensions、input schema 和配置 digest
都仍然匹配时，旧向量才能复用。

Vector object 必须满足：

- 文件名和内容 SHA-256 一致；
- 长度严格等于 `dimensions * 4`；
- 使用 little-endian IEEE-754 float32；
- 所有值 finite；
- 不能是全零向量。

### 6.5 `qlkg-store-operation-receipt-v1`

Snapshot、verify 和 materialize 返回同一 bounded/self-digested receipt。它把
以下维度分开，避免一个“success”掩盖降级：

- `integrity_status: integrity-valid`；
- `portable_status: ready | partial | unmanaged`；
- `retrieval_status: retrieval-ready | retrieval-not-ready`；
- `materialization_status: not-checked | materialized | current`；
- `semantic_status: not-checked | semantic-search-ready |
  semantic-search-not-ready`；
- `working_state: current`。

`graph-committed/embedding-pending` 和 `graph-committed/portable-pending` 属于
更高层 ingest/deploy orchestration，不会被一次已经完成的 store operation
伪装进该 receipt。

Snapshot 另外携带 `changed`、`root`、`mode`；materialize 携带 `database` 和
`materialized`。`warnings` 只使用 bounded stable codes，`receipt_sha256` 绑定除
自身外的 canonical receipt，包括完整 coverage payload。

Receipt 只包含 digests、bounded counts、coverage summary 和稳定 reason，不含
authority 原文、vector bytes、local profile、base URL 或 credential value。
Readiness gate 未满足时 CLI 在 stdout 返回 receipt 并使用 exit 3；损坏、schema
或 I/O failure 使用 exit 1 和 stderr 上的结构化错误。Argparse usage 保留 exit 2。

## 7. 核心工作流

### 7.1 原地创建或刷新

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT store snapshot --require-ready
kgdistiller --repo-root PROJECT store verify --require-ready
```

`snapshot` 首先确认 authority 与 committed graph 一致，并确认现有 SQLite
属于相同 graph generation。它不会调用模型，只导出 SQLite 中已经存在且
input digest 仍然有效的 node embeddings。

如果新节点尚未生成 embedding，required coverage 会低于 policy threshold，
普通 snapshot 不安装候选 generation。只有显式 `--allow-partial` 才能发布
`partial`；它仍然报告 `retrieval-not-ready`。默认 policy 不存在时保留 legacy
兼容路径，但 generation 必须标为 `unmanaged`。

### 7.2 从现有项目创建独立 store

```sh
kgdistiller --repo-root SOURCE store snapshot --output STORE --require-ready
kgdistiller --repo-root STORE store verify --require-ready
```

输出目录会收到：

- 图谱 generation 中列出的 authority 文件；
- registry、optional identities 和 alignments；
- 完整 graph artifacts 和 entry shards；
- document inventory 和 embedding bundle；
- `knowledge/.gitignore`，前提是目标中尚无该文件。

已有 `.gitignore` 永远不会被覆盖。已有输出文件若没有合法 store manifest，
snapshot 会拒绝覆盖，避免误伤非 kgdistiller 数据。

### 7.3 Git 同步

推荐在 private repository 中追踪：

- authority files；
- `knowledge/sources.json`；
- optional `knowledge/identities.json`；
- `knowledge/alignments.json`；
- `knowledge/graph/`；
- `knowledge/documents.jsonl`；
- `knowledge/embeddings/`；
- `knowledge/store.json`；
- `knowledge/.gitignore`。

应忽略：

- `knowledge/build/`；
- SQLite、WAL 和 journal；
- ingest staging、plan、receipt 和临时 snapshot；
- credentials、API keys、query logs 和 provider caches。

Git 操作的状态必须明确区分：

- `integrity-valid`：仅表示 portable bytes 与 manifest 一致；
- `retrieval-ready`：额外表示 required policy coverage 已满足；
- `local-only`：generation 尚未确认进入一个 commit；
- `committed-locally`：仅表示包含该 generation 的 commit 已创建；
- `remote-confirmed`：仅在 push 成功并确认 remote ref 包含该 commit 后使用。

生成 store 不等于完成备份。本地磁盘损坏场景只有在 commit 已存在于其他
存储介质或远端后才真正得到覆盖。

### 7.4 新机器恢复

```sh
git clone PRIVATE_REMOTE personal-kb
cd personal-kb
kgdistiller store verify --require-ready
kgdistiller store materialize
kgdistiller agent status
```

`materialize` 的顺序是：

1. 验证 store manifest schema 和 digest；
2. 验证 graph、registries 和所有 authority hashes；
3. 验证 document inventory；
4. 验证 embedding manifest、records、provider configs 和 vector objects；
5. 从 hydrated graph 原子重建 SQLite；
6. 导入 exact vectors；
7. 写入 store/embedding generation metadata；
8. 再次运行时，如果 generation 相同则直接 no-op。

恢复文档向量不需要 embedding provider。语义查询本身仍可能需要 provider
生成当前 query vector；这与重新计算所有 node vectors 是不同的成本。

### 7.5 入库后的行为

SQLite 原子重建以前会删除全部 embeddings。本次实现改为：

1. 重建前读取当前 embedding rows；
2. 为新 snapshot 中的每个 node 重新计算 canonical input digest；
3. 只保留 namespace、node、provider、model、dimensions 和 digest 仍然匹配
   的 exact vector bytes；
4. changed 或 deleted nodes 的向量自然失效；
5. 新节点在 provider 实际生成向量之前保持 missing。

`ingest-kgdistiller` Skill 在检测到 `knowledge/store.json` 后，会在成功事务
之后运行 `store snapshot` 和 `store verify`。如果 store 尚不存在，它会把
状态报告为 local-only，并建议使用部署 Skill；不会静默执行 Git init、
commit 或 push。

## 8. 完整性、安全和失败边界

### 8.1 Path safety

所有 manifest 路径必须是 repo-relative、非空且不含 `..`。解析后路径必须
仍然位于 store root 下。Embedding object 也经过相同的 resolved-path 检查，
避免通过 symlink 或构造路径读取 store 外部内容。

### 8.2 Staged publication、manifest-last 和恢复

Snapshot 在私有 staging 中生成 document inventory、embedding bundle、policy
binding、coverage 和候选顶层 manifest，并在安装前完整验证。Coverage gate 失败
会丢弃 staging，旧 manifest 及其引用 bytes 保持不变。

安装由单 writer lock 串行化，使用 durable publication journal 和 backup。顶层
`store.json` 仍是最后的 commit point；如果进程在多文件刷新中途终止，下一次
snapshot、verify 或 materialize 会先恢复上一份完整 generation。Stale cleanup
只在新 manifest 成为 commit point 后执行，不能破坏 last-known-good。

### 8.3 Stale artifact cleanup

内容寻址对象和独立输出目录中的过期 authority 会被有限清理。删除范围被
限制在上一个 manifest 声明且符合已知生成路径的文件。对于从独立 store
中移除的旧 authority，如果目标副本已经被本地修改，刷新会拒绝删除并
要求人工处理。

### 8.4 Integrity 不等于 authenticity

SHA-256 能发现意外损坏和 generation 混合，但不证明文件来自可信作者。
Private Git remote 的访问控制、传输安全、磁盘加密、commit signing 和
备份策略仍由用户环境负责。

### 8.5 隐私

Authority 文件和 embedding 可能泄露个人知识内容或文本特征，因此 portable
store 应默认使用 private repository 或合适的加密备份。任何情况下都不能
提交到 kgdistiller engine repository，也不能提交 provider keys。

## 9. 已实现功能

### 9.1 Core

- `src/kgdistiller/store.py`
  - store snapshot、verify 和 materialize；
  - canonical JSON/JSONL；
  - content-addressed float32 objects；
  - generation digests；
  - path、schema、record 和 object validation；
  - stale managed artifact 的受限清理；
  - exact embedding import。
- `src/kgdistiller/agent.py`
  - 新增 `qlkg-node-embedding-text-v1`；
  - 暴露 canonical embedding input digest；
  - index rebuild 按 digest 保留仍然有效的 vectors；
  - 记录 embedding provider configurations 和 retrieval lane。
- `src/kgdistiller/cli.py`
  - `store snapshot [--output STORE] [--require-ready|--allow-partial]`；
  - `store verify [--require-ready]`；
  - `store materialize [--require-ready]`。
- `src/kgdistiller/project.py`
  - 新项目在缺失时创建 `knowledge/.gitignore`；
  - 永不覆盖已有 ignore policy。

### 9.2 Versioned schemas

- `qlkg-store-v1.schema.json`；
- `qlkg-store-v2.schema.json`；
- `qlkg-store-operation-receipt-v1.schema.json`；
- `qlkg-document-record-v1.schema.json`；
- `qlkg-document-record-v2.schema.json`；
- `qlkg-embedding-bundle-v1.schema.json`；
- `qlkg-embedding-record-v1.schema.json`；
- `qlkg-embedding-bundle-v2.schema.json`；
- `qlkg-embedding-record-v2.schema.json`。

这些 schema 会包含在 wheel 和 sdist 中，并在 store verify 时由本地
deterministic JSON Schema evaluator 执行。

### 9.3 Agent Skills

- 新增 `deploy-kgdistiller`：负责布局选择、创建、refresh、verify、Git 边界、
  clone/pull 后的 materialize，以及准确的同步状态报告；
- 更新 `ingest-kgdistiller`：成功入库后刷新已经存在的 portable store。

### 9.4 文档

- README 增加 portable Git store 快速流程；
- deployment 文档增加 dedicated repository、Git/LFS、恢复演练和隐私边界；
- Agentic KB spec 增加 portable store 与 embedding contract；
- release compatibility matrix 保留四个 v1 schema，并增加当前 embedding
  bundle/record v2；
- transactional ingest 文档说明 embedding carry-forward。

## 10. 测试与验收

本次实现覆盖：

- 独立输出 store 的创建和完整验证；
- 从缺失或不同 generation 的 SQLite 状态重建；
- 不调用 provider，逐字节恢复 exact embedding rows；
- 相同 store generation 的第二次 materialize 为 no-op；
- tampered vector object 被 digest validation 拒绝；
- 独立 store 刷新时清理未修改的 stale authority；
- index rebuild 保留 unchanged node vectors；
- changed node input 只失效对应 embedding；
- CLI `store verify` 的真实子进程路径；
- `knowledge/.gitignore` 的创建和不覆盖行为；
- 四个 JSON Schema 随 Python distribution 打包。

发布级验证命令：

```sh
uv run python -m unittest discover -s tests -v
uv build
```

当前验收结果为 80 个单元测试全部通过，wheel 和 sdist 构建成功，两个相关
Skills 均通过 `quick_validate.py`。

## 11. 第一版已知限制

1. 一个 vector 一个文件适合个人小型知识库；当节点数量增长到数十万时，
   Git 小文件开销需要重新评估。
2. 第一版只持久化 node embeddings，不持久化 query vectors、query logs 或
   embedding-similarity soft edges。
3. Store 只有在显式 `--allow-partial` 后才可以发布 partial coverage；缺失向量
   需要在具备 provider 的机器上通过 `embedding sync` 补齐。
4. Git 同步不是实时同步。两台机器同时入库时，仍然需要先 pull/rebase，
   解决 authority 冲突，再生成并提交一个完整的新 generation。
5. `store snapshot` 不会自动 commit 或 push。这是有意的权限边界。
6. Dedicated `--output` 只复制 graph generation 注册的 `.md`、`.typ`、`.tex`
   authority；其他 authored assets 需要用户按项目策略额外管理。
7. 第一版没有内建 remote health check、定期备份调度或 Git hosting 集成。

## 12. 后续演进方向

只有在真实数据规模或工作流证明需要时，才考虑：

- 评估 generation-directory 是否能进一步简化 journal-based publication；
- 为大量向量增加 deterministic binary shards 和 compaction；
- 增加 `store diff`，解释两个 generation 的 document/node/vector 变化；
- 在不扩大 receipt 的前提下增加跨 generation coverage diff；
- 在明确授权下增加 Git remote backup health check；
- 为多机器写入增加 lock/lease 或更明确的 pull-before-ingest gate；
- 支持用户选择的加密对象存储或 Git LFS adapter，但不改变 canonical
  JSON/JSONL contract；
- 增加定期 restore drill，而不仅仅是定期 push。

第一版的关键原则保持不变：authority generation 可读、可 diff、可验证、
可由 Git 搬运；SQLite 可随时重建；embedding 可以被精确保留，但永远不能
取代显式 marker、reviewed identity 和 source-backed semantic evidence。
