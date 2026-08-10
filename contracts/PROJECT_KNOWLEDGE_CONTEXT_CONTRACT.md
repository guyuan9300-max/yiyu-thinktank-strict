# 项目知识上下文查询合同 v1

状态：`AMENDED_FOR_BLUEPRINT_88`

冻结日期：2026-07-30

## 1. 用途

`ProjectKnowledgeContext` 是工作台、任务详情和后续上下文消费者共同使用的
只读查询合同。它不建立新的业务权威对象，只组合严格新版已经冻结的项目、
知识文档版本、正式产物、证据关系和本机存储对象。

## 2. 统一输出

```text
ProjectKnowledgeContext
├── sandboxId
├── cloudInstanceId
├── organizationId
├── project
├── organizationSharedKnowledge[]
├── localPrivateKnowledge[]
├── materialBoundary
└── state
```

每条知识材料必须包含：

- `sourceScope`: `organization_shared | local_private`
- `sourceId`: 稳定对象 ID
- `sourceVersion`: 权威版本或本机来源版本
- `contentHash`: 有内容版本时返回 SHA-256；没有时为 `null`
- `summary`: 可供上下文使用的摘要或检索片段
- `sourceDescription`: 来源类型及发布边界说明
- `updatedAt`: 来源更新时间

## 3. 组织共享知识

组织共享知识只从当前会话可见的严格组织云 v2 权威对象读取：

- `clients` 的项目名称、摘要、生命周期和版本进入 `project`；读取资格统一由
  `secured_resources + object_grants + policy_versions` 裁决，禁止恢复
  `work_projects/project_participants`。
- `knowledge_documents + document_versions` 仅当文档
  `visibility_scope = organization`，且 `document_kind` 明确为
  `shared_summary`、`organization_shared_summary`、`project_narrative`、
  `report_summary`、`evidence_summary` 或其他以 `_summary` 结尾的安全摘要类型时，
  才能把 `preview_text` 作为共享摘要返回。
- `narrative_outputs + narrative_output_versions` 仅返回已明确保存的 active/stale
  组织权威产物。其当前版本 `content_markdown` 可以作为有限长度的项目叙事或
  正式报告上下文节选进入 `summary`；正文为空时才使用非空 `change_summary`。
  查询不得暴露 `content_markdown` 字段本身、`content_json` 或未保存的生成结果。
  这里返回的是已经保存的组织产物，不是其证据源文件正文。
- `evidence_links` 只返回稳定关系、版本和对象标题组成的关系说明，不返回来源正文。

组织共享查询不得返回：

- `source_locator`、本机路径或对象存储地址。
- 知识文档的 `markdown_content`、叙事产物的 `content_json`、未保存内容或
  原始文件字节。已保存叙事只以有限长度 `summary` 返回，不暴露存储字段。
- 未发布、仅 self/department/participants 可见的资料摘要。
- 仅凭项目名、文件名或当前用户猜测出来的关系。

## 4. 本机私有知识

工作台资料生产链路使用当前 sandbox 的 `storage_objects` 登记受管文件和可重建摘要。
本查询只消费工作台已经生成的记录，不负责上传、复制、解析、OCR 或转写源文件。
每个来源文件具有稳定 `object_id`、内容 SHA-256、媒体类型、大小、版本和更新时间。

- 文件必须由用户明确选择，并明确关联稳定 `project_id`。
- 受管文件只保存在当前设备，不创建云端 `source_assets` 或
  `knowledge_documents`。
- 可提取文字的文件生成有限长度检索片段；暂不能解析的图片、音频等材料明确标记
  `metadata_only`，不得伪造已经理解正文。
- `ProjectKnowledgeContext` 不返回原始路径；工作台文件视图可在本机内部使用受管路径
  完成打开文件操作。
- 本机私有知识不得进入组织云请求、业务快照、AI 保存回执或同步 outbox。

工作台生产者与本查询的最小交接格式为：

- 来源文件和摘要 sidecar 都位于
  `local-project-materials/<sandbox-hash>/<project-hash>/` 受管目录下，并分别登记为
  当前 sandbox 的 `storage_objects`。
- 摘要记录的 `media_type` 固定为
  `application/vnd.yiyu.project-knowledge-summary+json`。
- sidecar JSON 的 `schema` 固定为
  `yiyu.project-local-private-knowledge.v1`，`sourceScope` 固定为
  `local_private`，并至少包含 `projectId`、`sourceId`、`contentHash`、
  `summary`、`summaryKind`、`sourceDescription`、`updatedAt` 和 `fileName`。
- `sourceId` 必须引用同一 sandbox、同一项目受管目录中的活动来源
  `storage_objects.object_id`；`contentHash` 必须与来源记录一致。
- 查询校验摘要 sidecar 自身的 SHA-256 和大小，并确认来源仍是受管目录内、
  大小一致的本机文件；为避免每次打开工作台扫描大文件，来源正文不在查询时重新读取。
- 本查询遇到越界路径、缺失来源、hash 不一致或空摘要时，必须报告
  `failed_retryable`，不得把异常记录当作成功或普通空结果。

## 5. 身份、隔离与失败状态

- 本机归属只认请求开始时捕获的 `sandbox_id`。
- 组织身份只认同一 WorkspaceContext 中的
  `cloud_instance_id + organization_id`。
- 项目只按稳定 `project_id` 查询，不按名称匹配。
- 迟到回包不得更新或显示到另一个 sandbox。
- 两类来源分别返回 `ready | empty | blocked | failed_retryable | not_connected`。
- 只有两类查询均完成时，整体状态才可为 `ready` 或 `empty`；任一来源未接通或失败
  必须保留真实状态，不能把另一来源的数据包装成完整成功。

## 6. 明确非目标

- 本合同不上传、下载或同步源文件正文。
- 本合同不替代工作台的解析、提炼和“发布为组织共享”命令。
- 本合同不把本机摘要写成组织权威知识。
- 本合同不为任务模块另建知识库；任务只按 `project_id` 消费该统一输出。

## 7. 单组织云部署与验收

本查询没有 DDL、迁移或真实数据改写。部署仍必须逐个组织云进行：

1. 在目标服务器只读执行 `systemctl status/cat` 和 `nginx -T`，重新确认实际服务名、
   `WorkingDirectory`、环境文件、数据库路径、私有端口和公开 `/api/v2` 代理；不得用
   历史记录替代核验。
2. 分别备份目标组织自己的严格 v2 数据库、服务配置、nginx 配置和待替换源码。
   备份不得复制到另一组织云。
3. 只部署严格仓库的 `cloud_backend/` 兼容改动以及本合同；本次无
   `strict_common`、schema manifest 或迁移变化。
4. 重启经核验的服务后，先检查 health、handshake、manifest、
   `cloudInstanceId` 和 `organizationId`，再用当前组织有效会话读取
   `GET /api/v2/projects/{project_id}/knowledge-context`。
5. 验收响应必须满足：
   - 云实例、组织和稳定 `project_id` 与当前 WorkspaceContext 完全一致。
   - 日慈项目能返回项目元数据和真实共享知识数量。
   - 响应递归检查不含 `sourceLocator`、`storageKey`、路径字段、
     `markdownContent`、`contentMarkdown`、`contentJson`、`rawContent`
     或 `fileContent`。
   - `materialBoundary` 的四项云端禁止标志全部为 `false`。
   - 无共享摘要时返回 `empty`；接口不存在时本机显示 `not_connected`；
     5xx/超时显示 `failed_retryable`，三者不得互换。
6. 先完成益语云及日慈验证，再对星丛重复同一流程；不得复制数据库、
   组织 ID、Cloud instance 或会话凭据。
7. 回滚只恢复该组织云的已备份代码和配置并重启原服务；本次没有数据库回滚步骤。
