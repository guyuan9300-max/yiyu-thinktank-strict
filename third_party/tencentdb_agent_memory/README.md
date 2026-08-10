# TencentDB Agent Memory 上游源码快照

本目录只保存益语后续适配所需的上游原件，不是运行时入口，也不是第二套业务系统。

## 固定来源

- 官方仓库：`https://github.com/Tencent/TencentDB-Agent-Memory.git`
- Chat Memory 主分支：`3c6fc6425f22d24c4917dc3b7791a175d2d13545`
- Skill / Wiki 团队 Beta：`b44c6db5f5b1a011eed645efb1949840f99f961a`
- 许可证：MIT；两份上游 `LICENSE` 均随快照保留。

逐文件来源、SHA-256 和能力分类见 `UPSTREAM_SNAPSHOT.json`。被筛掉的依赖所留下的相对导入已机械登记在 `ADAPTATION_IMPORT_GAPS.json`；环节 2 的逐项去向见 `ADAPTATION_DECISIONS.json`，不能靠重新搬入上游数据库或身份实现来消除。

## 本轮搬入范围

### Chat Memory

来自主分支 `src/core` 和 `src/offload` 的分层记忆、抽取、去重、画像、场景、召回、提示词、解析器、符号化和文本处理算法；只保留存储接口及检索算法，不搬上游 SQLite、Tencent VectorDB 或网关实现。

### Skill

来自团队 Beta `MemoryCore/src/core/skill` 的 Skill 格式、提取、权限判断、版本、队列、对话提炼和存储接口；不搬 `skill-store-ddl.ts`、`skill-store.ts` 或 TCVDB Skill Store。

### Wiki

来自团队 Beta `MemoryKnowledge/src/engines/wiki` 的切片、文件协议、模板、合并、索引构建、关系检索和知识页生成算法；不搬 `index-db.ts` 和直接创建外部模型客户端的 `ingest-v2/llm.ts`。

Wiki 的 `graph-search.ts` 用于知识关系召回，不是被产品裁决排除的 CodeGraph 软件代码图谱。

## 明确未搬入

- `MemoryPanel/**` 与任何上游前端界面；
- `MemoryProxy/**`、Gateway、Metadata Server 和全局模型代理；
- 上游独立用户、团队、项目和权限体系；
- SQLite / TCVDB 业务权威、DDL、迁移和数据库服务；
- CodeGraph 引擎及 `@colbymchenry/codegraph`；
- OpenClaw、Hermes 等宿主适配器；
- 上游路由、部署脚本、Docker 和控制台。

## 使用边界

1. `upstream/` 下文件保持上游原貌，不在其中加入益语业务判断。
2. 当前快照尚未加入构建或运行入口；其中指向被排除模块的 import 将在环节 2 由益语适配接口替换。
3. 正式事实、权限、版本和审计只能落入严格新版 88 表；上游缓存只能是可删除、可重建的引擎缓存。
4. 后续升级必须重新固定提交、生成哈希差异并人工选择，不得直接覆盖本目录。

## 当前移植状态

截至 2026-08-05，已完成的是“移植准备层”：上游能力源码与许可证已冻结，严格 88 表端口、六个功能 Agent、组织模型适配和本地/组织 Wiki 边界已经建立。上游 Wiki 分块器已有宿主适配；Chat Memory、Skill 及 Wiki 的其余算法仍是后续链路施工时选用的冻结参考源码，尚未成为运行时能力。

因此，“上游文件已落仓”不得表述为“前端功能已接通”或“全部上游算法已运行”。L0—L3 提取与召回、跨 Agent 共享、Skill 导入与权限、Wiki 关系和索引，应在对应黄金链中按用户体验逐项启用。
