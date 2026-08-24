#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="${1:-}"
OUTPUT_DIR="${2:-$HOME/Desktop/$VERSION}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "用法：npm run release:mac-signed -- <新版本号> [输出目录]" >&2
  echo "示例：npm run release:mac-signed -- 0.29.6 \"$HOME/Desktop/0.29.6\"" >&2
  exit 2
fi

[[ "$(git branch --show-current)" == "main" ]] || { echo "必须在 main 分支发版。" >&2; exit 3; }
[[ -z "$(git diff --name-only --diff-filter=U)" ]] || { echo "仍有未解决冲突，禁止发版。" >&2; exit 4; }

REMOTE_URL="$(git remote get-url origin)"
[[ "$REMOTE_URL" == "https://github.com/guyuan9300-max/yiyu-thinktank-strict.git" || "$REMOTE_URL" == "git@github.com:guyuan9300-max/yiyu-thinktank-strict.git" ]] \
  || { echo "origin 不是严格新版权威仓库：$REMOTE_URL" >&2; exit 5; }

# npm 同步修改 package.json 与 package-lock.json，避免人工改漏版本号。
npm version "$VERSION" --no-git-tag-version --allow-same-version >/dev/null
git add -A
npm run verify:strict-maintenance

git fetch origin main
if ! git merge-base --is-ancestor origin/main HEAD; then
  echo "远端 main 含本机尚未接入的提交，请先快进接收后再发版。" >&2
  exit 6
fi

if ! git diff --cached --quiet; then
  git commit -m "release: prepare $VERSION"
fi
git push origin main
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git ls-remote origin refs/heads/main | /usr/bin/awk '{print $1}')"
[[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]] || { echo "main 推送后远端提交未对齐。" >&2; exit 7; }

# 只有 main 已经对齐才开始构建签名安装包。
npm run dist:mac-signed
SOURCE_DMG="$ROOT_DIR/dist/yiyu-thinktank-strict-$VERSION-arm64.dmg"
[[ -f "$SOURCE_DMG" ]] || { echo "未找到最终 DMG：$SOURCE_DMG" >&2; exit 8; }
/bin/mkdir -p "$OUTPUT_DIR"
FINAL_DMG="$OUTPUT_DIR/益语智库AI（新版）-$VERSION-arm64.dmg"
/bin/cp -f "$SOURCE_DMG" "$FINAL_DMG"
bash scripts/verify_mac_update_installer.sh "$FINAL_DMG"

echo "main: $REMOTE_HEAD"
echo "DMG: $FINAL_DMG"
/usr/bin/shasum -a 256 "$FINAL_DMG"
