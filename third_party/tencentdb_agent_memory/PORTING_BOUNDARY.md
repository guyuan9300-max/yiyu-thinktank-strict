# 益语移植边界（环节 2）

`upstream/` 是逐文件哈希冻结的上游原件，永不直接进入益语构建或运行时。真正可被益语调用的唯一边界是：

`src/shared/agentMemoryPorts.ts`

## 已清除的第二权威入口

- 不加载上游 SQLite、Tencent VectorDB、MongoDB、Metadata Server 或 DDL；
- 不调用上游用户、团队、项目、会话和权限解析；
- 不从环境变量、配置文件或全局单例推断当前成员、组织、client 或 Agent；
- 不创建 OpenAI、Anthropic、OpenClaw、Hermes 或其他全局模型客户端；
- 不调用 MemoryPanel、MemoryProxy、Gateway 或上游路由；
- 不允许 Skill 直接执行任意脚本。

## 唯一宿主输入

每次调用必须由益语传入完整宿主身份：

`sandboxId + cloudInstanceId + organizationId + principalId + agentId + scopeKind`

- `scopeKind=client` 时必须传入真实 `clientId`；
- `scopeKind=principal/organization` 时 `clientId` 必须为 `null`，不得为了个人 L3 画像或组织共享 Skill 制造假项目。

缺少任何一项立即失败，不按组织名、云地址、当前用户或最近项目猜测。

## 唯一宿主能力

- 正式记忆事实只能经 `AgentMemoryFactAuthorityPort`；
- Skill 只能经 `AgentSkillAuthorityPort`；
- Wiki 构建结果只能经 `AgentWikiProjectionPort`；
- 模型调用只能经 `AgentMemoryModelPort`，上游不得接触模型密钥；
- 引擎临时结果只能经 `AgentMemoryRebuildableCachePort`，缓存不得覆盖正式事实；
- 所有写入必须携带 `operationKey`，可变事实必须携带期望版本；
- 所有结果必须经 `AgentMemoryAuditPort` 留下真实终态。

## L0 边界

原对话、文件正文和本地路径不通过云端事实端口。后续本地实现只能在当前 sandbox/client 的本地权威对象中处理；同步仅允许发送产品裁决批准的安全摘要、事实、版本与证据引用。

## 下一环节

环节 3 已在 `src/shared/agentMemoryStrict88Adapter.ts` 建立严格 88 表、六个内置 Agent 和组织默认模型适配。当前两端仍各为 88 表，但物理就绪检查明确缺少：

- `source_sets.client_id`
- `ai_answers.client_id`
- `ai_answers.bot_id`
- `bot_definitions.agent_kind`

完整机械结果见 `STRICT_88_BINDING_STATUS.json`。在对应黄金链正式补字段、集中构建和迁移前，适配器必须返回 `agent_memory_schema_not_ready`，禁止借用其他字段藏值。

环节 4 已通过 `src/shared/agentMemoryService.ts` 暴露统一能力接口：

- 记忆：写入、召回、撤销；
- Skill：创建、列出已启用版本、启用/停用；
- Wiki：构建切块投影、检索、来源失效。

Wiki 分块算法由 `src/shared/agentMemoryWikiChunker.ts` 从冻结的团队 Beta MIT 源码适配。当前没有任何 renderer、main、后端路由引用该服务；四个必要字段未补齐时，服务在访问数据层前即返回 `agent_memory_schema_not_ready`。

## 环节 5 移植准备层验收（2026-08-05）

移植准备层已完成最小验收：

- 四个宿主适配模块可同时导入，六个内置 Agent、严格 88 表绑定、作用域校验和 Wiki 分块可实例化；
- `npm run build:main` 与 `npm run build:renderer` 首次通过；
- 使用独立临时数据目录启动严格本地后端，`/api/v2/health` 返回 `ready`，schema 为 `yiyu-blueprint-88-v1`；临时目录已移入废纸篓，未读取或修改安装版真实数据；
- 本地、云端 manifest 仍各为 88 表；没有新增第 89 张表；
- 冻结上游 97 个文件逐一复核，SHA256 无差异且没有符号链接；
- 宿主运行时代码没有引用 `upstream/`，根依赖没有引入 TencentDB Agent Memory、独立 SQLite、向量库或 CodeGraph；
- 四个宿主适配模块未出现 DDL、`/api/v1`、环境变量取密钥、直连网络或第二身份体系入口。
- 29 张能力绑定表均进入就绪门检查；当前本地、云端 manifest 均只缺已裁决的四个待补字段，不会因漏检可靠性、权限或索引表而误报 ready；
- Wiki 构建已拆成 `localWiki` 与 `organizationWiki` 两套端口，本地原件不能通过组织知识投影端口处理；
- Wiki 分块的目标字符数现在是硬上限，重叠内容不能把下一块撑破上限。

当前正确终态仍是 `blocked_schema_extension_pending`：四个已裁决字段未随准备工作偷加，统一服务在数据层访问前返回 `agent_memory_schema_not_ready`。后续只能在对应黄金链施工时，通过严格 builder、manifest 和离线迁移补齐字段，再逐项接入前端功能；不得把本次“可导入、可构建、可启动”解释为功能链路已经接通，也不得表述为 Chat Memory、Skill、Wiki 全部算法已经适配运行。
