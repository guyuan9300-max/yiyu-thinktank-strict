# 益语智库蓝图 88 表物理合同 v7

状态：`FROZEN_FOR_IMPLEMENTATION`

冻结日期：2026-08-05

## v6 到 v7 的唯一结构变化

- `source_sets.client_id`：把资料集合固定到客户/项目；允许为空以兼容不属于项目的通用集合。
- `ai_answers.client_id`：把回答固定到客户/项目；允许为空以兼容非项目回答。
- `ai_answers.bot_id`：记录实际执行回答的功能 Agent；外键指向 `bot_definitions.id`。
- `bot_definitions.agent_kind`：登记六个组织作用域内置功能 Agent；普通机器人同事保持为空。
- 表数仍为本地 88、云端 88；不新增通用 JSON 字段、不新增知识库或记忆权威表。

## 六个内置功能 Agent

- 唯一登记源：`agent-memory-builtins.v1.json`。
- 身份由 `organization_id + agent_kind` 稳定生成，写入云端 `secured_resources` 与 `bot_definitions`。
- 内置功能 Agent 属于组织作用域，不归属某个员工；机器人同事仍按原有成员/负责人归属规则登记。
- 本地只接收云端投影，不得自行创造内置 Agent 权威行。
- 六个 Agent 共用组织默认模型配置；本合同不复制模型密钥，也不建立独立模型代理。

## 约束与迁移

- `bot_definitions(scope_id, agent_kind)` 在 `agent_kind IS NOT NULL` 时唯一。
- `agent_kind` 只允许登记源中的六个值或 `NULL`。
- 内置 Agent 的 owner 两列必须同时为空；普通机器人同事仍必须且只能有一个 owner。
- v6→v7 只能在副本离线重建并检查后替换；运行时禁止 DDL。
- 旧 v6 行按同名字段完整迁入，新字段为空；只有云端迁移器为规范组织作用域补齐六个内置 Agent。
- `source_sets`、`ai_answers` 的项目绑定与 Agent 绑定由后续黄金链命令合同按业务场景强制，本轮不伪造历史归属。

## 不代表已接通

本合同只完成 Agent Memory 主线的物理底座。字段存在、Agent 已登记或迁移通过，不代表 GC-07、GC-10、GC-14 等前端功能已接通；它们仍须按 17 个环节逐步验收。
