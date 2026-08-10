# 益语智库严格新版可靠性合同 v1

状态：`FROZEN_FOR_IMPLEMENTATION`

## 1. 命令链

所有写入必须形成同一条可追踪链：

`前台动作 -> WorkspaceContext -> command_envelope -> 权限/CAS/幂等 -> 权威事务 -> outbox -> 外部副作用 -> 回执 -> 投影刷新`

`WorkspaceContext` 至少冻结：

- `sandbox_id`
- `cloud_instance_id`
- `organization_id`
- `principal_id`
- `membership_id`
- `device_id`
- `request_id`

后台任务捕获发起时的不可变上下文，运行中切换空间不得改变写入目标。

## 2. CAS 与版本

- 每个可修改权威对象必须有单调递增版本。
- 更新命令必须携带 `expected_version`。
- 版本不符返回冲突和当前最小快照，不得 last-write-wins。
- 旧回包不得覆盖新状态。
- 批量命令必须逐项记录 expected version 和结果，不能用整体成功掩盖部分失败。

## 3. 幂等

幂等范围固定为：

`cloud_instance_id + organization_id + actor_principal_id + command_type + idempotency_key`

同键同载荷返回原回执；同键不同载荷必须拒绝。幂等记录保存输入指纹、结果指纹、状态、首次和最后尝试时间，不保存敏感明文。

## 4. 生命周期

生命周期是权威事实，不从更新时间、是否在列表中或删除标记猜测。

通用生命周期至少区分：

- `active`
- `paused`
- `completed`
- `archived`
- `cancelled`
- `revoked`
- `deleted`
- `missing`

域对象可收窄状态集合，但必须定义合法转换、操作者、版本和恢复路径。物理删除只用于法规或确定性清理，并保留审计墓碑。

处理状态与生命周期分离。例如 `queued/parsing/failed` 不能冒充材料已撤销或已删除。

## 5. Outbox、Inbox 与外部副作用

- 权威事务和 outbox 登记在同一数据库事务中完成。
- 事务内不得访问飞书、对象存储、模型服务、邮件或其他网络。
- worker 按稳定资源 ID 执行，支持重试、退避、去重和死信。
- 外部失败不得回滚已经确认的权威事实，除非业务合同明确要求补偿命令。
- inbox 负责去重外部回调；回调不得按名称寻找业务对象。
- 每个副作用保存 provider resource ID、请求指纹、最近回执和补偿状态。

## 6. 审计

所有权威写入必须记录：

- actor、workspace 和组织身份
- command/operation/request ID
- 对象类型与稳定 ID
- 前后版本
- 动作与结果
- 时间
- 非敏感差异摘要
- 失败类别

审计日志追加写，不作为业务读模型，不保存 token、密码、API Key、附件正文或完整模型提示词。

## 7. 权限

- 云端是组织权限真源。
- 每次权威读取和写入都根据当前成员关系、资源归属和权限版本判定。
- 前端权限只用于显示，不是安全边界。
- 缓存的权限投影必须带来源版本；版本未知时不得放行。
- 管理员、管理层、部门负责人和成员的信息可见范围与系统操作权限分开计算。

## 8. 密钥

- 本地密钥进入 macOS Keychain、Windows Credential Manager 或对应系统密钥库。
- 云端密码使用强哈希；组织 AI Key 使用服务端加密封装。
- 日志、SQLite、renderer、错误堆栈和 API 响应不得出现明文密钥。
- 密钥按云实例、组织和用途隔离。
- 组织身份未验证时不得复用缓存密钥。

## 9. 存储对象

对象文件和业务记录分离，但必须通过稳定 `storage_object_id`、内容 SHA256、大小、媒体类型和生命周期关联。

上传完成必须经过：

1. 临时对象写入。
2. 哈希与大小校验。
3. 权威记录确认。
4. 对象转为 active。

孤儿对象进入 reconciliation，不得按文件名自动挂到业务对象。原始文件缺失必须显示 `missing_source`，不得伪装为解析失败。

## 10. 恢复与对账

- 数据库、对象存储和部署配置必须形成同一 recovery set。
- recovery set 保存数据库哈希、对象清单哈希、schema/build identity 和创建时间。
- 恢复演练必须在隔离目录完成，并验证数据库、关键对象和身份握手。
- reconciliation 只报告或执行确定性修复；有歧义时进入问题清单。
- 任何自动修复不得按名称、云地址或当前用户猜测归属。

## 11. 失败呈现

前台必须区分：

- 正在加载。
- 成功且有数据。
- 成功但确实为空。
- 权限或状态阻止。
- 失败且可重试。
- 功能尚未接通严格新版。

错误必须贴近具体操作。一次 AI、同步或外部集成失败不得锁住下一次独立操作。

