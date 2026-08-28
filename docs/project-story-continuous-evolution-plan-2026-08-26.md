# 项目 Story 持续积累、对比、校验与发布实施方案

日期：2026-08-26

状态：架构与实施计划，尚未实施

适用仓库：益语智库AI（新版）严格88表正式仓库

## 一、目标结论

不需要增加第89张表，也不需要另建一套“Story数据库”。现有88表已经具备资料版本、事实、证据、关系、来源集合、叙事版本、提案审批、幂等、Outbox、审计和对账基础。

真正需要补齐的是一条唯一工作流：

```text
日常任务 / 计划 / 会议 / 文件 / 纠错 / 官网更新
  → 保存原始业务事实并发出领域事件
  → 提取候选陈述，绑定项目、来源和版本
  → 去重、对比、冲突识别
  → 人工或受控规则核验
  → 生成 Story 更新提案
  → 发布前逐条校验
  → 原子发布唯一 Story 新版本
  → 任务 AI 和其他下游只读该已发布版本
```

这里最重要的边界是：日常工作只能积累“证据”和“候选事实”，不能直接改写 Story；AI 只能起草 Story 更新提案，不能把自己生成的叙述直接变成事实。

## 二、四类对象必须严格分开

### 1. 原始业务记录

任务、计划、会议纪要、录音转写、文件、官网页面和人工纠错，各自继续写入现有权威表。它们回答“谁在什么时间记录了什么”，不等于其中每句话已经被证明。

### 2. 候选事实

从原始记录中抽取的、可能影响项目 Story 的陈述，进入 `atomic_facts`，初始状态为 `candidate`。每条候选都要通过 `evidence_links` 和 `source_set_members` 绑定到精确来源 ID、来源版本和定位信息。

### 3. 已核验事实

只有通过授权成员确认或明确、可审计的自动核验规则后，才能变为 `verified`。事实权威属于 `atomic_facts`，不属于 Story 文本。

### 4. 项目 Story

Story 是根据已核验事实和已发布证据生成的派生产物。每个项目只有一个稳定的 Story 聚合，但可以有连续版本。Story 可以被重建、撤销或标记过期，绝不能反向覆盖事实底稿。

## 三、不同日常材料怎样进入系统

| 日常动作 | 可以自动确认的内容 | 不能自动确认的内容 | 默认处理 |
| --- | --- | --- | --- |
| 新建或修改任务 | 任务由谁在何时建立、当前字段和状态 | 任务目标已经实现、业务效果已经发生 | 记录为意图或候选事实 |
| 任务标记完成 | “该任务已被某成员标记完成” | 交付质量、客户接受、实际经营结果 | 系统状态可核验；成果仍需验收证据 |
| 发布计划 | 组织在该版本中作出的计划和决定 | 计划已经完成或外部结果已经达成 | 作为已发布意图，不冒充结果 |
| 会议纪要确认 | 参会人确认这份纪要代表会议内容 | 会议中的预测、传闻和外部承诺一定为真 | 形成候选事实和行动项；高影响陈述需复核 |
| 文件发布 | 文件版本、作者、发布时间和原文内容 | 文件内的每项主张都客观成立 | 抽取候选事实并保留页段定位 |
| 合同、验收、系统回执 | 经授权上传且满足规则的确定字段 | 超出凭证范围的推论 | 可按白名单规则核验有限事实 |
| 人工纠错或补充 | 授权成员明确确认的陈述 | 未经授权的个人推断 | 按权限和影响级别进入核验链 |
| 官网更新 | 白名单官网在特定版本公开的内容 | 官网未陈述的推论 | 保留URL、抓取时间、内容哈希和版本 |

核心原则：系统可以自动确认“记录本身发生过”，但不能自动把“记录里声称的结果”升级成客观结果。

## 四、现有88表如何复用

- `tasks / planning_cycles / meetings / transcription_versions / knowledge_documents / document_versions`：继续保存各业务领域的权威原始记录。
- `content_chunks`：保存可定位的内容切片，便于从长文件或转写中引用精确段落。
- `atomic_facts`：保存候选、已核验、已拒绝事实；Story 只允许使用有效的 `verified` 事实。
- `evidence_links`：把每条事实绑定到任务、计划、会议、转写或文件的精确版本与定位。
- `relationship_triples`：记录事实之间的“重复支持、补充、更新、取代、冲突、无关”等关系，不另建对比表。
- `source_sets / source_set_members`：冻结每次对比、每个 Story 提案和每个已发布版本实际使用的来源快照。
- `ai_proposals / ai_approvals`：保存 Story 更新提案、风险级别、审核结论和批准规则。推荐把未发布草稿放在提案中，不直接移动正式 Story 指针。
- `narrative_outputs`：保存每个项目唯一的正式 Story 聚合，固定 `artifact_kind='project_story'`。
- `artifact_versions`：只保存正式 Story 的不可变版本历史和内容哈希；批准前不占用正式版本号。
- `derivation_lineage`：记录每个 Story 版本由哪个来源集合、事实版本和生成器产生；来源失效时据此精确失效。
- `commands / idempotency_records / operation_attempts / outbox_events / inbox_receipts / saga_operations`：负责幂等、重试、弱网、迟到回包和崩溃恢复。
- `audit_events / execution_runs / reconciliation_runs`：记录谁触发、谁批准、后台是否完成以及最终数据是否收敛。
- `secured_resources / object_grants / policy_versions`：沿现有统一权限链裁决组织、项目、成员和资料可见性，不在 Story 代码中另写客户专属判断。

## 五、积累、对比和校验的具体算法

### 1. 先把来源标准化

每次业务写入成功后，在同一事务写入领域 Outbox 事件。事件至少携带：`scope_id`、项目 ID、来源对象类型、来源对象 ID、来源版本、操作 ID、事件时间和安全的内容哈希。

后台消费者按项目合并短时间内的重复事件。幂等键由“组织 + 项目 + 来源对象 + 来源版本 + 处理策略版本”稳定生成；重复投递和迟到回包只回放同一结果。

### 2. 抽取候选陈述

将材料拆成独立、可判断真假的最小陈述，而不是整段摘要。例如：

- “2026年8月26日发布了AI运营后台计划”是一条陈述；
- “AI运营后台已经可投入生产”是另一条陈述；
- 两者必须分别处理，不能因为计划存在就推导产品已上线。

每条候选保存规范化主体、关系、客体、有效时间、事实哈希、来源版本和提取器版本。原始文字和定位保留在受权限保护的 manifest/evidence 中。

### 3. 与现有事实逐条对比

对同一项目、同一业务对象和相近有效时间的事实进行比较，结果只允许落入以下类别：

- `duplicate`：内容相同，属于重复来源；
- `supports`：新来源支持已有事实；
- `extends`：补充已有事实但不改变原结论；
- `updates`：同一对象出现较新状态；
- `supersedes`：新事实明确取代旧事实；
- `contradicts`：两项陈述不能同时成立；
- `irrelevant`：与项目 Story 主线无关。

这些关系使用 `relationship_triples` 记录。相似度只能帮助分流，不能直接裁决真假。

### 4. 按风险核验

- 低风险：重复支持、格式修正、经批准的白名单系统状态，可由已批准自动化规则确认。
- 中风险：阶段变化、负责人变化、计划调整，需要相关负责人确认。
- 高风险：合同、金额、客户承诺、上线状态、经营结果、法律关系、组织身份和互相矛盾的来源，必须人工审核。

任何自动核验都要记录规则 ID、规则版本和适用范围。没有批准规则时默认进入人工审核，不做静默升级。

### 5. 生成 Story 更新提案

只有当已核验事实集合或已发布证据版本发生有效变化时才生成提案。提案必须同时包含：

- 新 Story 正文；
- 与当前已发布版本的差异摘要；
- 新增、修改、删除或仍待确认的陈述；
- 每条陈述引用的事实 ID、事实版本和来源集合；
- 当前知识截止时间、生成器版本、输入指纹和覆盖范围；
- 风险等级及是否需要人工批准。

若输入指纹与上一提案或已发布版本一致，直接幂等返回，不生成空版本。

### 6. 发布前硬校验

云端发布器重新检查：

1. 项目仍属于同一组织与权限作用域；
2. 所有引用事实仍为 active + verified；
3. 来源对象、版本和哈希仍存在且未撤销；
4. Story 中每条客观陈述都有有效引用；
5. 当前正式版本仍等于提案生成时的预期版本；
6. 高风险提案已有有权成员批准；
7. 内容不含本机路径、私有文件正文或越权资料。

任何一项失败都关闭发布，返回明确原因；不能删掉引用后继续发布，也不能回退到普通共享摘要。

### 7. 原子发布

批准后在一个云端事务内：

- 创建新的 `artifact_versions(published)`；
- 更新唯一 `narrative_outputs.current_version`；
- 固定 source set 和 lineage；
- 写入 command、idempotency、audit、execution run、reconciliation 和 Outbox 回执。

使用 expected aggregate version 做 CAS。旧提案、迟到的模型结果或并发发布不能覆盖较新的 Story。

## 六、Story 何时更新、何时不更新

不建议每次新建任务都立即重写 Story。正确策略是：

1. 每个业务动作立即积累来源与候选事实；
2. 相同项目的短时间事件合并处理，避免重复模型调用；
3. 只有已核验事实发生实质变化时才生成新提案；
4. 只有提案相对当前版本存在有意义差异时才发布新版本；
5. 普通措辞变化、重复材料和无关内容不产生 Story 版本。

这样既能持续积累，又不会让 Story 随着每条任务频繁抖动或越来越长。

## 七、失效、删除、纠错和恢复

- 来源被撤销、删除、权限代次变化或事实被拒绝时，先将对应 `derivation_lineage.invalidated_at` 置为有效时间，再发出项目级重建请求。
- 当前 Story 若依赖关键失效事实，应返回 `stale`；仍可保留历史版本供审计，但任务 AI 不得继续把它当成新鲜背景。
- 新事实与当前 Story 冲突时，先保留当前已发布版本，生成高风险更新提案；审核完成前不自动覆盖。
- 重建失败时通过 `operation_attempts` 重试；超过策略阈值进入明确失败/死信和人工处理，不做静默回退。
- 服务崩溃重启后，通过 pending Outbox、命令回执和 reconciliation 恢复，不能依赖前端页面状态。

## 八、任务 AI 和其他下游的读取合同

建立唯一的 `project_story(scope_id, project_id)` 仓储读取函数，只返回 active、published 的 `project_story` 当前版本。

`ProjectKnowledgeContext` 增加单值 `projectStory`，至少包含：

- `storyId`、`storyVersion`、`contentHash`；
- `state`（ready / stale / not_available / failed_retryable）；
- `updatedAt`、`knowledgeCutoff`、`sourceSetId`；
- 适合任务上下文的短摘要和可见引用回执。

任务 AI 只能读取该字段，不能再扫描 `organizationSharedKnowledge` 后把若干摘要拼成“正式 Story”。Story 不存在或过期时，保留用户原始任务说明并显示明确状态。

## 九、数据库合同的最小优化

下一版严格 schema 只需增加约束和索引，不增加表：

1. `narrative_outputs`：同一 `(scope_id, client_id)` 只能有一个 active 的 `project_story` 聚合；稳定 ID 仍作为第一层保证，数据库部分唯一约束作为第二层保证。
2. `artifact_versions`：增加 `(scope_id, artifact_id, version)` 唯一约束。
3. Story 关键父子关系补齐带 `scope_id` 的复合外键或等价写入守卫，防止跨组织引用。
4. `derivation_lineage`：阻止同一 Story 版本、同一 source set 和同一生成器重复建立有效血缘。
5. `evidence_links`：为同一事实、同一来源版本和同一定位增加去重约束，防止重试产生重复证据。

约束落地必须走离线迁移：只读预检重复/孤儿 → 独立备份 → 停写 → 迁移 → quick/integrity/foreign-key check → migration ledger → 重启。禁止运行时 DDL。

## 十、当前代码需要改造的主要位置

### 数据和工作流

- `cloud_backend/app/repositories/gc04_tasks.py`
- `cloud_backend/app/repositories/gc06_planning.py`
- `cloud_backend/app/repositories/gc08_meetings.py`
- `cloud_backend/app/repositories/project_materials.py`
- 新增或收敛一个项目 Story 后台编排仓储；不要在四个领域仓储中各写一套 Story 逻辑。

上述领域写入只负责发出统一的“项目证据已变化”事件；Story worker 负责抽取、对比、核验、提案和发布。

### Story 生成、审核和读取

- `backend/app/workbench_chat_local.py`：从“直接准备并发布战略画像”改为“生成安全 Story 提案”；禁止模型未返回引用时自动补上全部检索来源。
- `cloud_backend/app/repositories/gc14_strategic_profile.py`：拆分为提案校验与正式发布，不再重建后直接 published。
- `cloud_backend/app/repositories/gc14_proposals.py`：复用提案、审批和 CAS 能力，增加 `project_story_update` 操作类型与执行器。
- `cloud_backend/app/repositories/workbench_outputs.py`：增加精确 `project_story` 读取，不再在多种 narrative 类型中择一。
- `cloud_backend/app/repository.py`：让 `project_knowledge_context()` 返回单值 `projectStory`。
- `backend/app/ui_domains/workflow.py`：任务 AI 只消费 `projectStory`，删除共享摘要冒充 Story 的逻辑。

### Schema 与合同

- `contracts/PROJECT_KNOWLEDGE_CONTEXT_CONTRACT.md`
- `contracts/strict-cloud-schema-manifest.v1.json`
- `contracts/strict-local-schema-manifest.v1.json`
- `strict_common/schema.py`
- `strict_common/physical_schema.py`
- `scripts/migrate_strict_schema.py`

## 十一、建议实施顺序

### P0：先止损并冻结语义

1. 冻结 `project_story`、事实、证据、提案和发布合同。
2. 让任务 AI 停止把共享摘要当 Story；缺失时保持原说明。
3. 增加唯一 Story 读取接口和负向权限测试。

### P1：接通日常积累与对比

1. 让任务、计划、会议和资料发布事件统一触发项目证据变化。
2. 实现候选事实提取、证据绑定、去重和七类对比关系。
3. 建立风险分级核验队列；先不自动发布 Story。

### P2：接通提案、审批和原子发布

1. 生成带逐条引用和差异摘要的 Story 提案。
2. 高风险人工批准，低风险只允许经批准规则自动通过。
3. 以 CAS、幂等、Outbox、审计和对账发布正式版本。

### P3：受控回填与灰度

1. 先清理“星丛”现有候选事实：去重、分类、核验，不把旧摘要整体提升为 Story。
2. 生成星丛第一份正式提案，由顾源源审阅批准。
3. 发布成功后才开启任务 AI 消费，并逐组织灰度。

## 十二、真实验收矩阵

- 唯一性：并发创建和发布同项目 Story，最终只有一个正式聚合和一个当前版本指针。
- 幂等性：同一来源版本、重复 Outbox、弱网重试不会生成重复事实、提案或 Story 版本。
- 客观性：无引用陈述、candidate/rejected 事实、越权来源、失效来源均不能发布。
- 对比性：重复、支持、补充、更新、取代、冲突和无关七类样本均得到稳定结果。
- 审核性：高风险变化未批准时当前 Story 不变；拒绝后不会被后台重试再次发布。
- 时序性：迟到模型结果和旧 expected version 必须冲突关闭，不能覆盖新版本。
- 恢复性：在候选写入后、批准后、发布事务后、Outbox发送前分别模拟崩溃，重启后仍收敛到一个正确版本。
- 生命周期：来源撤销、恢复、删除、事实纠错、权限代次变化都能精确失效和重建。
- 隔离性：跨组织、跨项目、无授权成员、私有资料全部失败关闭且不泄露元数据。
- 消费性：真实任务创建回执能核对使用的 Story ID、版本和哈希；Story 缺失时绝不回退到共享摘要。
- 性能：Story 重建在后台异步完成；任务解析只读取预计算当前版本，不增加第二次模型等待。具体 P95 阈值需由产品验收合同另行冻结，未冻结前不得宣称性能通过。

## 十三、当前验收状态

当前只完成仓库级只读分析和实施计划。现有代码具备大量可复用基础设施，但“日常积累 → 对比 → 核验 → 提案 → 审批 → 唯一 Story 发布 → 下游消费”的完整真实闭环尚未实施，不能认定已经可用。
