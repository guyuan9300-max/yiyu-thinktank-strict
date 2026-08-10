# 益语智库蓝图 88 表物理合同 v8

## v7 到 v8 的唯一结构变化

- 两端仍各有且只有 88 张标准表，表名、字段和外键均不变化。
- `automation_rules.record_kind` 的冻结枚举增加 `agent_skill`。
- `agent_skill` 只承载声明式指令、输出模板、适用 Agent 和已登记工具引用；禁止保存或执行任意脚本。
- 六个内置 Agent 的核心岗位合同及版本继续由 `bot_definitions` 权威登记，Skill 不得覆盖岗位边界。

## 迁移与运行边界

- v7→v8 只能在数据库副本离线重建并检查后替换；运行时禁止 DDL。
- 迁移必须保持数据库 generation、全部88表业务行、外键和现有投影。
- 非88表、通用 payload 表、旧写作风格库均不得成为 Agent Skill 的第二权威。
