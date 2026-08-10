# GC-02 前项目兼容分支只读归档

这里保存 GC-02 环节1开始前仍留在活动模块中的旧项目实现原文：

- `cloud_backend/domain_routes/project_materials_legacy.py`
- `cloud_backend/repositories/project_materials_legacy.py`

归档只用于追溯，不属于 Python 包，运行时禁止导入、attach、读取或 fallback。
其中的 `work_projects`、`project_participants`、旧命令账本和旧知识对象均已被
蓝图88表合同取代。活动服务只注册 `GC07ProjectMaterialsRepository` 以及后续
GC-02正式实现。

归档时 SHA-256：

- domain routes：`b5b69c67ba870743f3fb17c582141bd335f96dad570400c7249a6c1b797bbe82`
- repository：`d3af2fc0c79b7618d2ee1dba15c4fbf763477c8363d8c244c029d26a4226637a`
