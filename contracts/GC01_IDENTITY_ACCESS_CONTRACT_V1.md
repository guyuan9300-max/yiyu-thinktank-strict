# GC-01 身份、组织、沙箱与权限投影合同 v1

状态：`CONTRACT_READY / RUNTIME_NOT_VERIFIED`

## 用户主路径

`打开软件 -> 登录/恢复会话 -> 选择真实组织沙箱 -> 确认 principal/membership/scope -> 获取 policy -> 生成 viewer projection -> 前端显示获准页面与能力`

本合同只覆盖已有组织的登录、重新登录、当前身份、工作空间列表、切换、权限可见面和退出。创建组织、加入陌生组织、邀请审批和个人空间不在本轮运行范围；相关入口必须准确显示 `not_connected` 或产品批准的阻断，不得造假组织。

## 顶层状态

唯一顶层状态集合为：

- `loading`：请求正在进行；不得先显示空结果。
- `ready`：身份、会话和权限租约有效。
- `empty`：查询成功且授权范围内确实没有对象。
- `blocked`：身份存在但明确不能继续，例如 `permission_denied`、`schema_incompatible`、`authorization_lease_expired`。
- `failed_retryable`：超时、网络或临时服务失败。
- `not_connected`：没有组织绑定、会话或已登记能力尚未接通。

陈旧度独立使用 `current | stale | expired | unknown`，并返回 `lastConfirmedAt`、`leaseExpiresAt`、`reasonCode` 和 `retryable`。历史 `denied` 映射为 `blocked/permission_denied`；历史 `error` 按是否可重试映射；历史 `stale` 不再作为顶层状态。

## 核心表

`principals`、`organizations`、`authorization_scopes`、`organization_memberships`、`sandboxes`、`policy_versions`、`viewer_projections`。

共用过程表仅在相应事实发生时使用：`idempotency_records`、`commands`、`outbox_events`、`derivation_lineage`、`audit_events`、`reconciliation_runs`。构建登记只写 `control_registry` 与 `query_registry`。

## 权威与投影

- principal、organization、membership、scope 和组织 policy 由组织云权威，本地保存带来源版本和租约的投影。
- `sandboxes` 逐行权威：本机 device/sandbox/local_session_snapshot 由本机权威；server_session 由组织云权威。
- viewer projection 由组织云 policy 与 membership 生成，本地只能投影，不得反向授予权限。
- 权限和功能接通状态必须相交判断：被授权但未接通显示 `not_connected`；功能已接通但无权限显示 `blocked/permission_denied`。
- 当前角色权限的机器合同为 `gc01-authorization-policy.v1.json`。未知角色只能降级到 member 最小权限，禁止按名称猜测或自动提升。
- 云端部署/成员权限变更事务负责生成或失效 viewer projection；登录和权限查询只读已生成投影，不得在 GET 中临时写入或重新猜权限。

## 读写与禁止边界

机器可读的逐入口边界见 `gc01-registry.v1.json`。所有 88 表中未出现在某入口 allowlist 的表，默认对该入口禁止；所有 88 表之外的表永远禁止。

GC-01 不得读取项目、任务、事件线、来源资料、知识、报告、AI 回答或复盘业务表。登录和切换完成后，其他黄金链可独立异步加载，但不得阻塞 GC-01 进入 `ready`。

## 切换与乱序

每个异步操作固定携带 `sandboxId + cloudInstanceId + organizationId + scopeId + requestSeq`。切换时立即隔离旧空间；上下文不匹配的迟到回包不得更新界面、提示、编辑器或目标沙箱。

## 会话与秘密

明文 access/refresh token 只能进入 Keychain 或服务器 secret store。数据库只保存哈希、引用、指纹、到期时间和状态。退出必须撤销云端 server_session，再清除本机 secret reference；部分失败进入准确可重试状态。

## 过程记录保留

- `commands` 的 scope、actor、target、idempotency 和 payload hash 等核心信封不可变；仅 `status/settled_at` 可按合同 CAS 更新。
- `outbox_events` 的 event identity/body hash 不可变；仅 `status/published_at` 可按合同 CAS 更新。
- 两表无普通业务删除；达到保留期限且 legal hold 解除后，只能由 `purge_ledger` 证明受控物理清除。

## 当前完成边界

本合同和登记完成只允许把 GC-01 入口标记为 `contract_ready`。在益语云、本地运行时、renderer 和安装版真实点击完成前，禁止标记 `runtime_verified` 或 `released`。
