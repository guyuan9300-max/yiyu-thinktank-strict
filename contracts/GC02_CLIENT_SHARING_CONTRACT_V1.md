# GC-02 客户创建、分享、权限传播与撤权合同 v1

状态：`RUNTIME_VERIFIED`

## 用户主路径

`创建客户项目 -> 编辑项目元数据 -> 明确选择组织成员 -> 云端形成对象授权 -> 被分享成员读取项目与获准共享知识 -> 撤权 -> 所有新读取、召回与导出 fail closed`

本合同中的“客户”与“项目”是同一个 `clients` 对象。不得恢复
`work_projects`、`project_participants` 或按名称拼装的第二套项目身份。

## 权威与投影

- 组织云 `clients` 是项目元数据和生命周期权威；本地同名表只保存当前沙箱最后确认投影。
- `secured_resources` 为被保护项目的同 ID 授权根。
- `object_grants + policy_versions` 是分享和撤权的唯一权威；成员列表不是第二份共享事实。
- `viewer_projections`、搜索/向量索引清单、AI 上下文、缓存与导出授权都是可重建派生面，不得反向授予权限。
- `organization_memberships` 是受让主体来源；禁止按姓名、邮箱、项目名或当前用户猜主体。

## 明确能力边界

项目负责人或组织管理员可以维护项目元数据与分享范围。被分享成员的首版能力集合为：

- `read`：读取项目元数据和合同明确允许的组织共享知识、客户档案与官网事实；
- `contributeKnowledge`：问答、纠错、补充同一项目协作知识；
- 不含 `write`：不得修改项目元数据；
- 不含 `manageSharing`：不得转授或撤销他人；
- 不含本机原件读取：不得读取其他成员的文件正文、原路径或本地存储定位符。

父项目授权不自动扩散到任意子资源。各消费者只有在本登记明确声明消费项目授权时，才能使用上述能力；报告下载、Skill、个人记忆等资源仍按各自合同决定是否需要独立 grant。

## 命令与冲突

- 创建、编辑、分享、撤权和归档均携带稳定幂等键。
- 编辑、分享和撤权携带 `expectedVersion`；CAS 冲突返回 `blocked/version_conflict`，不得覆盖较新事实。
- 分享更新采用差量命令：未变化成员不得撤销后重建；新增成员增加新 generation，移除成员只撤销当前 active grant。
- 每条 active grant 必须引用 active `policy_versions`，并记录单调 `grant_generation`。
- 权威事务同时写 `commands`、`idempotency_records`、`audit_events` 和必要 `outbox_events`。
- 幂等结果的可校验回执使用既有 `object_manifests(storage_kind=command_receipt)`；
  该对象只保存命令结果回执，不承载第二份项目或授权事实。

## 传播与撤权

授权生效后，工作台、战略陪伴、共享知识、AI 上下文、报告和合同明确允许的导出必须经过同一项目授权门。撤权事务提交后：

1. 新的项目详情与列表读取立即拒绝；
2. `viewer_projections` 立即失效；
3. 搜索、向量、AI 上下文、缓存和导出派生沿 `derivation_lineage` 失效或待重算；
4. 历史回答与审计仍可保留，但不得成为新的越权召回来源；
5. 单个异步消费者失败不回滚撤权，必须登记可重试结算状态。

物理 purge、法律保留和恢复窗口属于 GC-15，本合同只负责撤权与派生失效。

## 本机与跨设备边界

- 本地查询固定绑定请求开始时捕获的 `sandboxId + cloudInstanceId + organizationId + scopeId + clientId + requestSeq`。
- 切换项目、组织或沙箱后，旧请求迟到回包不得更新列表、资料、回答、提示或编辑器。
- 记忆同步只允许安全 manifest：ID、类型、版本、内容哈希和更新时间；L0 对话、回答正文、文件正文、本机路径与密钥永不上云。
- 项目访问由组织云当前成员关系和项目授权在线校验；历史租约时间只作兼容诊断，断网时组织业务读写不可用，不使用过期本地投影绕过授权。

## 状态合同

- `loading`：正在请求或传播；不得先显示假空。
- `ready`：权威命令或查询完成且当前授权有效。
- `empty`：查询成功且授权范围内确实没有项目或资源。
- `blocked`：无权限、版本冲突、租约过期或合同明确阻止。
- `failed_retryable`：网络、超时或传播暂时失败。
- `not_connected`：相关分享、Skill 或跨设备同步能力尚未接通。

## 88 表边界

逐入口读写表见 `gc02-registry.v1.json`。所有未列入入口 allowlist 的88表默认禁止；88表之外永远禁止。特别禁止：

- `work_projects`、`project_participants`、`projection_business_objects`；
- `/api/v1`、旧数据库 ATTACH、旧库 fallback；
- 通用 `payload_json/metadata_json` 作为第二业务权威；
- 将组织云文件名伪装成本机可访问文件。

## 当前完成边界

合同和登记完成只允许标记 `contract_ready`。只有双账号 Electron 完成“创建、分享、读取/补充、撤权、刷新重启拒绝”，并证明派生消费者失效且数据库仍恰好88表，GC-02 才能标记 `runtime_verified`。

2026-08-07 已完成上述双账号 Electron 纵向验收，并完成本地/益语云当前活动库的 88 表结构复核，状态正式更新为 `runtime_verified`。完整证据见 `output/gc02-phase11/GC02_RUNTIME_VERIFICATION_20260807.md`。
