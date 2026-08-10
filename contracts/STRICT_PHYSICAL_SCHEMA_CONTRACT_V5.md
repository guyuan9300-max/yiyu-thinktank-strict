# 益语智库蓝图 88 表物理合同 v5

状态：`SUPERSEDED_BY_V6`

> 88 表物理结构未废弃；活动合同已升级为 `STRICT_PHYSICAL_SCHEMA_CONTRACT_V7.md`；v6 仍作为 GC-01 与不可变过程记录规则的历史冻结证据。

冻结日期：2026-08-04

## 唯一活动结构

- 本地活动库和组织云活动库各自恰好包含 manifest 中同名的 88 张表。
- 表名、字段、类型、唯一约束、外键及删除规则以两个完整 physical manifest 为唯一机器合同。
- 表存在不代表功能接通。功能是否可用只由黄金链验收和 capability 回执决定。
- 每一行只能有一个权威侧；另一侧只能保存带来源版本和投影状态的投影。

## 切换边界

- v4 活动库必须先做完整备份，然后仅在副本上离线重建 v5。
- 只迁入身份、组织结构、有效登录引用和组织集成配置。
- 当前测试项目、任务、事件线、旧知识及旧操作流水不迁入活动库。
- 原数据库作为独立只读归档保留；运行时禁止 `ATTACH`、读取或 fallback。
- 旧 24/81 表仓储代码必须脱离应用启动入口，保留在 `legacy_frozen/v4/` 供追溯。

## 黄金链恢复规则

- 15 条黄金链必须逐条声明前端入口、预期读表、预期写表、禁止表和失败五态。
- 接通只能使用 88 表；确需加表必须重新取得产品裁决并升级 manifest。
- 未完成安装版真实验收的链路不得标记为 connected。

## 机器合同

- 本地：`strict-local-schema-manifest.v1.json`
- 云端：`strict-cloud-schema-manifest.v1.json`
- schema family：`yiyu-blueprint-88-v1`
- contract version：`5`
