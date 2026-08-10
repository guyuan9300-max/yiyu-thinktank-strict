# AGENTS.md - 益语智库严格新版

## 核心不变量

- 本仓是严格新版根历史，不得 merge、cherry-pick 或复制旧仓数据库实现。
- 本地业务归属只认 `sandbox_id`。
- 组织身份只认 `cloud_instance_id + organization_id`。
- 运行时代码不得执行 DDL；DDL 只允许集中 schema builder 执行。
- 所有数据库表必须进入冻结 manifest。
- 不得读取旧数据库路径、调用 `/api/v1`、使用旧表 fallback 或新旧双读。
- 未接通业务必须返回 `not_connected`，不得显示假空数据。
- renderer 和 route 层不得直接执行 SQL。
- token、密码和 AI Key 不得进入 SQLite、renderer 或日志。

## 修改要求

- 新增业务域前先冻结权威对象、生命周期、权限、CAS、幂等、迁移和 schema manifest 增量。
- 运行 `npm run audit:strict`、`npm run test:strict` 和完整构建后才能提交。
- 真实数据库、附件、密钥、`.env` 和阶段备份不得进入 Git。

