# 益语智库蓝图 88 表物理合同 v6

状态：`FROZEN_FOR_IMPLEMENTATION`

冻结日期：2026-08-04

## v5 到 v6 的唯一变化

- 88 张表、字段、类型、唯一约束、索引和外键完全不变。
- `commands` 与 `outbox_events` 的删除规则从无法执行的通用墓碑文字，改为 `append_only_retention_purge`。
- GC-01 的用户界面状态统一为 `strict-six-state-v1`；陈旧度独立表达，不建立第二套顶层状态机。
- `control_registry` 与 `query_registry` 开始写入 GC-01 的确定性构建登记；这不代表运行链路已经验证完成。

## 唯一活动结构

- 本地活动库和组织云活动库各自恰好包含 manifest 中同名的 88 张表。
- 表名、字段、类型、唯一约束、外键及删除规则以两个完整 physical manifest 为唯一机器合同。
- 每一行只有一个权威侧；另一侧只能保存带来源版本、投影状态和租约的投影。
- 表存在、登记存在或接口返回 200 都不等于功能接通；功能只有经过对应黄金链安装版验收才能标记 `released`。

## 黄金链恢复规则

- 每条黄金链必须声明稳定前端入口、查询/命令、预期读表、预期写表、禁止表、权限、失败状态和恢复动作。
- 活动代码只允许访问 88 表；运行时禁止 DDL、旧表、旧接口、`ATTACH` 和 fallback。
- 普通组件修改的结构预期为零变化；确需结构变化必须重新取得产品裁决并升级 manifest。

## 机器合同

- 本地：`strict-local-schema-manifest.v1.json`
- 云端：`strict-cloud-schema-manifest.v1.json`
- GC-01：`GC01_IDENTITY_ACCESS_CONTRACT_V1.md`
- GC-01 登记源：`gc01-registry.v1.json`
- schema family：`yiyu-blueprint-88-v1`
- contract version：`6`
