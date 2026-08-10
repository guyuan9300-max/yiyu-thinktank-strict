# 益语智库严格新版业务数据合同 v2

状态：`SUPERSEDED_BY_BLUEPRINT_88 / LEGACY_REFERENCE_ONLY`

冻结日期：2026-07-29

> 2026-08-06 裁决：本文件记录7月29日严格v2阶段的历史业务对象，已经被
> `STRICT_PHYSICAL_SCHEMA_CONTRACT_V8.md`、蓝图88表manifest及逐黄金链合同取代。
> 运行时禁止使用本文件中的 `work_projects`、`project_participants`、
> `task_records` 等旧物理名；当前项目唯一权威为 `clients`，分享唯一权威为
> `secured_resources + object_grants + policy_versions`。本文件不得再作为施工SOT。

## 1. 目标

本合同在创世底座之上接入真实业务数据。严格新版不读取旧数据库，
但必须能够承载并显示旧版已经确认的项目、任务、事件线、资料、计划、
周复盘、成长证据和正式产物。

旧数据库只作为一次性迁移输入。运行时不得打开旧库、调用 `/api/v1`
或通过旧表 fallback 补齐结果。

## 2. 项目边界裁决

- 工作台项目是唯一项目权威对象，物理表为 `work_projects`。
- 旧 `clients` 仅在离线迁移中按稳定 ID 转换为 `work_projects`。
- 不建立 `clients` 兼容表，也不保留“客户壳”和“项目”两套身份。
- 组织默认内部项目是普通项目的一种受保护属性，不是组织身份。
- 组织仍只由 `cloud_instance_id + organization_id` 识别。

## 3. 权威对象

### 项目与任务

- `work_projects`：项目名称、简介、颜色、默认内部项目及生命周期。
- `project_participants`：项目共享关系。
- 新建事件线必须先选择稳定项目；旧数据中项目记录已缺失的事件线保留为
  `unassigned`，在用户明确补齐项目之前，不得执行依赖项目的写入命令。
- `task_records`：任务正文、时间、优先级、状态和版本。
- `task_collaborators`：唯一负责人、协作者及接受/已阅状态。
- `task_lists/task_list_memberships`：任务清单及任务归属。
- `task_tags/task_tag_assignments`：标签及任务标签关系。
- `task_activity_events`：不可变任务活动。
- `task_return_notices`：负责人退回并删除任务后留给发起人的待阅事实。

任务可暂时没有项目；事件线必须有项目。任务与事件线的正式关系只在
`event_line_task_links` 中保存，不在任务行另存第二份关系。

### 来源资料与证据

- `source_assets`：组织云中原始文件的稳定身份、校验和和生命周期。
- `knowledge_documents/document_versions`：可追溯的文档及解析版本。
- 历史组织资料若项目记录已经缺失，保留为 `unassigned`，不得猜到默认项目、
  丢弃或按名称重建归属。
- `processing_attempts`：上传、解析、转写等处理尝试。
- `evidence_links`：资料与任务、事件线或产物之间的明确证据关系。

分块、搜索、向量和摘要均为可重建投影，不是权威事实。

### 事件线

- `event_line_records`：项目、创建人、归属部门、目标、背景和生命周期。
- `event_line_participants`：显式参与者。
- `event_line_task_links`：任务关联及人工里程碑。
- `event_line_activities`：明确关联产生的活动。
- `event_line_attachments`：事件线补充材料。

不得按名称、客户、标题关键词或第一条记录自动建线、挂线或写活动。

### 计划、复盘、成长与产物

- `organization_plans/organization_plan_items`：组织或部门计划。
- `weekly_reviews/weekly_review_sections/weekly_review_task_links`：周复盘事实。
- `intelligence_records/intelligence_revisions`：情报候选及人工修订。
- `growth_signals/growth_evidence`：成长信号及证据。
- `narrative_outputs/narrative_output_versions`：主线和正式报告的统一产物版本。
- `ai_answers/workbench_favorites`：工作台回答及用户明确收藏。

AI 生成内容默认是派生结果或候选；只有明确保存、确认或确定性规则才
能进入上述权威对象。

## 3.1 合同版本 4：组织模型语义权威

合同版本 4 在不恢复旧表、旧库或双读的前提下，补齐旧版组织设置界面
已经存在但版本 3 没有权威承载的字段：

- `organization_records` 承载年度目标、年度战略、季度重点和组织负责人；
  组织季度计划仍由 `organization_plans` 承载。
- `organization_departments` 承载颜色、父级、使命、业务/团队背景、季度
  重点和协作部门；部门负责人仍以 `department_memberships` 为唯一权威，
  部门季度计划仍由 `organization_plans` 承载。
- `management_titles` 承载岗位层级、适用部门、上下级、职责、协作岗位和
  任务权限；机器人或真人持岗仍统一使用
  `management_title_memberships`。
- `organization_memberships` 承载成员项目角色、当前重点和任务权限；直属
  上级只从 `organization_reporting_lines` 投影，禁止另存第二份。
- `organization_reporting_lines`、`organization_task_control_rules` 和
  `organization_role_process_templates` 分别作为汇报关系、任务控制规则和
  角色流程模板的组织云权威。
- `organization_membership_applications` 承载当前成员申请调整部门、岗位和
  工作重点的待审批事实；管理员审批后才更新成员、部门或岗位权威，拒绝
  不能伪装成停用成员。
- 组织和部门介绍资料继续使用
  `knowledge_documents/document_versions`。源文件只保留在成员本机；云端
  只接收用户明确保存的正文、摘要和内容哈希，不接收或读取本机路径。

组织模型整体保存以 `organization_records.version` 作为聚合 CAS，命令
必须带稳定幂等键；事务同时更新子对象、审计和 outbox。所有引用的成员、
部门、岗位、机器人和文档必须逐项验证属于当前组织，层级和汇报关系不得
自指或成环。

## 4. 本地边界

组织业务事实以云端为权威。本地只使用
`projection_business_objects` 保存按 `sandbox_id` 隔离、可删除重建的
业务投影。投影必须包含来源版本、生命周期、刷新时间和完整 payload。

本地待写操作继续使用 `command_envelopes` 和幂等回执，不建立影子业务表。
迟到回包只能更新发起操作时捕获的 sandbox。

## 5. 命令、权限和生命周期

- 所有写入携带组织、操作者、幂等键和 `expectedVersion`。
- 云端命令事务同时写权威对象、审计和必要 outbox。
- 查看范围以当前成员、部门、管理层、项目参与和对象参与关系统一解析。
- 生命周期至少区分 active、completed、archived、cancelled、revoked 和 missing。
- 删除默认是可审计归档或撤销；真正清除必须走独立清除合同。

## 6. 迁移

- 迁移程序独立于正式运行代码。
- 输入是只读旧库与当前严格创世库；输出是全新 v2 数据库。
- 保留可验证的旧稳定业务 ID，并把旧成员 ID 映射到严格
  `membership_id`，不按姓名猜测。
- 无法确认组织、项目、成员或父对象的行进入迁移隔离报告，不写运行库。
- 搜索、向量、缓存和旧 AI 派生快照不迁移。
- 迁移前后记录逐表数量、内容哈希、外键、隔离项和来源血统。

## 7. 前台状态

只有真实查询成功且结果为空时显示空态。未迁移、无权限、合同不兼容、
同步失败和处理失败必须分别显示 blocked、not_connected 或
failed_retryable。业务 capability 只有在云端查询、命令、本地投影和
前端均接通后才能宣告 connected。
