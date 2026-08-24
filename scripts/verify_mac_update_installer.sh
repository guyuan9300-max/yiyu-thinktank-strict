#!/bin/bash
set -euo pipefail

DMG_PATH="${1:?用法：bash scripts/verify_mac_update_installer.sh <dmg路径>}"
DMG_PATH="$(cd "$(dirname "$DMG_PATH")" && pwd)/$(basename "$DMG_PATH")"
[[ -f "$DMG_PATH" ]] || { echo "安装包不存在：$DMG_PATH" >&2; exit 2; }

WORK_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/yiyu-installer-verification.XXXXXX")"
MOUNT_DIR="$WORK_DIR/mounted"
MOUNTED=0
cleanup() {
  if [[ "$MOUNTED" == "1" ]]; then
    /usr/bin/hdiutil detach "$MOUNT_DIR" -force >/dev/null 2>&1 || true
  fi
  /bin/rm -rf "$WORK_DIR"
}
trap cleanup EXIT

/bin/mkdir -p "$MOUNT_DIR" "$WORK_DIR/Applications"
/usr/bin/codesign --verify "$DMG_PATH"
/usr/bin/hdiutil attach -quiet "$DMG_PATH" -nobrowse -readonly -mountpoint "$MOUNT_DIR"
MOUNTED=1

INSTALLER="$MOUNT_DIR/安装或更新益语智库AI.app"
PAYLOAD="$INSTALLER/Contents/Resources/Payload/益语智库AI（新版）.app"
TARGET="$WORK_DIR/Applications/益语智库AI（新版）.app"
[[ -d "$INSTALLER" && -d "$PAYLOAD" ]] || { echo "DMG 不是安装或更新结构。" >&2; exit 3; }
[[ ! -e "$MOUNT_DIR/益语智库AI（新版）.app" ]] || { echo "禁止回退到拖拽安装结构。" >&2; exit 4; }

/usr/bin/codesign --verify --deep --strict "$INSTALLER"
/usr/bin/codesign --verify --deep --strict "$PAYLOAD"
EXPECTED_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PAYLOAD/Contents/Info.plist")"
EXPECTED_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PAYLOAD/Contents/Info.plist")"
[[ "$EXPECTED_BUNDLE_ID" == "com.yiyu.thinktank.strict" ]] || { echo "软件身份错误：$EXPECTED_BUNDLE_ID" >&2; exit 5; }

# 在隔离目录造一个同身份旧版，调用 DMG 内真正的更新程序完成替换。
# 这一步不接触本机 /Applications，也不会打开测试副本。
/usr/bin/ditto "$PAYLOAD" "$TARGET"
/usr/libexec/PlistBuddy -c 'Set :CFBundleShortVersionString 0.0.0-verification' "$TARGET/Contents/Info.plist"
/usr/bin/touch "$TARGET/Contents/Resources/old-version-marker"
"$INSTALLER/Contents/MacOS/安装或更新益语智库AI" --noninteractive --verification-target="$TARGET"

ACTUAL_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$TARGET/Contents/Info.plist")"
ACTUAL_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$TARGET/Contents/Info.plist")"
[[ "$ACTUAL_VERSION" == "$EXPECTED_VERSION" ]] || { echo "覆盖后仍是旧版本：$ACTUAL_VERSION" >&2; exit 6; }
[[ "$ACTUAL_BUNDLE_ID" == "$EXPECTED_BUNDLE_ID" ]] || { echo "覆盖后软件身份漂移：$ACTUAL_BUNDLE_ID" >&2; exit 7; }
[[ ! -e "$TARGET/Contents/Resources/old-version-marker" ]] || { echo "旧版内容未被完整清除。" >&2; exit 8; }
if /usr/bin/find "$WORK_DIR/Applications" -maxdepth 1 \( -name '.*.previous-*.app' -o -name '.*.update-*.app' \) -print -quit | /usr/bin/grep -q .; then
  echo "覆盖后遗留了旧版备份或更新临时副本。" >&2
  exit 9
fi
/usr/bin/codesign --verify --deep --strict "$TARGET"
echo "覆盖安装验证通过：0.0.0-verification -> $ACTUAL_VERSION"
