# 严格新版分域配置与组织机器人合同 v3

状态：`FROZEN_FOR_IMPLEMENTATION`

本合同补足严格云业务表没有承载的组织配置、个人配置和组织机器人，不得据此
恢复旧表、旧接口或双读线路。

## 1. 配置作用域

- `organization`：组织管理员写；组织有效成员只读脱敏元数据，服务端可消费密文。
- `personal`：当前成员本人读写；其他成员和管理员不得冒充修改。
- 个人配置优先于同组织同种类配置；这是同一权威表内的确定性选择规则，
  不是旧库 fallback。
- 任务、工作台、主题、手册、转写与纯界面偏好只允许 `personal`。
- 飞书、语音和对象存储允许 `organization` 与 `personal` 两种作用域。
- `system_admin` 等授权政策继续使用 authorization policy/grant，不得塞入偏好。

配置主表为 `scoped_configuration_records`。公开配置进入
`public_config_json`；长期密钥只以 `SecretCipher` 生成的密文和不可逆指纹保存。
API、命令 envelope、审计、outbox 和 renderer 均不得出现长期明文密钥。

## 2. 机器人身份

机器人是 `principal_kind = bot` 的稳定 principal，并拥有本组织 active membership。
机器人权限继续使用既有 authorization resource/policy/grant；任务协作继续使用既有
membership/task 表。`organization_bot_profiles` 只保存机器人档案、能力策略和
强哈希 token。token 明文只允许在创建或轮换成功的单次响应返回。

机器人创建、修改、停用、轮换 token 和授权由组织管理员执行；组织有效成员可以
读取获准的机器人目录。机器人任务计划以 `bot_task_plans` 为权威对象，审批与执行
状态写入必须满足权限、CAS、幂等、审计和 outbox。

## 3. 维护模式

维护模式仅允许稳定 `organization_id = org_yiyu_default` 的 active member 使用，
不按组织名、云地址或当前用户猜测。它是本机协作会话开关，不属于机器人或配置
权威表；云端只验证成员资格并保存审计事实。

## 4. 迁移

云 schema 从 manifest
`6cbe155474234f90ca59307b46efa078fc39c3dedea4ff65eca8242248d665b0`
升级时，必须先生成恢复集和数据库备份，再由离线有序迁移创建三张新表、为
`identity_principals` 增加 `principal_kind`，写入 migration/build identity，
完成表集合、外键、manifest hash 与双组织隔离验证后才允许服务启动。
运行时请求处理禁止 DDL。
