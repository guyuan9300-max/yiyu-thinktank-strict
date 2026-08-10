# 益语智库AI（新版）

这是益语智库蓝图 88 表严格实现仓，拥有独立 Git 根历史、应用身份、数据目录和 API v2 合同。

0.29.1 是“88 表底座＋15 条黄金链”的首个统一协作基线，严格范围为：

- 本地与云端各自恰好包含同名 88 张标准表。
- 每张表按裁决账本分别承担权威或投影角色。
- 身份、组织、登录、组织配置和主要业务链均已接入严格对象。
- 旧业务数据留在独立只读归档，不进入活动库。
- 旧 24/81 表运行时代码已脱离启动入口。
- 15 条黄金链已完成代码接线和首轮纵向验证，后续按真实 Electron 体验继续修正产品细节。

“表存在”不等于功能已接通。功能完成仍以真实前端入口、权威读写轨迹和安装版验收为准。本仓只允许 `/api/v2` 和 88 表 manifest，不读取归档库、不调用旧接口、不双读 fallback；尚未完成的外部能力必须返回准确状态，不得伪造空数据或成功。

业务数据必须通过仓库外的一次性迁移程序写入严格新对象。迁移程序和旧库均不得进入安装包或正式运行服务器。

## 本地开发

```bash
npm ci
uv sync
npm run dev
```

严格云候选：

```bash
YIYU_STRICT_CLOUD_BOOTSTRAP_TOKEN=change-me \
YIYU_STRICT_CLOUD_MASTER_KEY="$(uv run python scripts/generate_cloud_key.py)" \
uv run python -m cloud_backend.app.main --data-dir ./tmp/cloud
```

## 验证

```bash
npm run test:strict
npm run audit:strict
npm run build
npm run package:mac-local
npm run test:packaged
```

阶段二验收证据见
[`docs/strict-candidate-acceptance-20260729.md`](docs/strict-candidate-acceptance-20260729.md)。
