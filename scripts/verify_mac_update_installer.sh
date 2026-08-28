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

/bin/mkdir -p "$MOUNT_DIR" "$WORK_DIR/Applications" "$WORK_DIR/LegacyApplications"
/usr/bin/codesign --verify "$DMG_PATH"
/usr/bin/hdiutil attach -quiet "$DMG_PATH" -nobrowse -readonly -mountpoint "$MOUNT_DIR"
MOUNTED=1

INSTALLER="$MOUNT_DIR/安装或更新益语智库AI.app"
PAYLOAD="$INSTALLER/Contents/Resources/Payload/益语智库AI（新版）.app"
LEGACY_BRIDGE="$MOUNT_DIR/益语智库AI（新版）.app"
TARGET="$WORK_DIR/Applications/益语智库AI（新版）.app"
LEGACY_TARGET="$WORK_DIR/LegacyApplications/益语智库AI（新版）.app"
[[ -d "$INSTALLER" && -d "$PAYLOAD" ]] || { echo "DMG 不是安装或更新结构。" >&2; exit 3; }
[[ -d "$LEGACY_BRIDGE" && -f "$MOUNT_DIR/.yiyu-update-legacy-bridge" ]] || { echo "DMG 缺少 0.29.4 更新兼容载荷。" >&2; exit 4; }
[[ ! -L "$MOUNT_DIR/Applications" ]] || { echo "DMG 不得提供拖拽安装链路。" >&2; exit 5; }

/usr/bin/codesign --verify --deep --strict "$INSTALLER"
/usr/bin/codesign --verify --deep --strict "$PAYLOAD"
/usr/bin/codesign --verify --deep --strict "$LEGACY_BRIDGE"
EXPECTED_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PAYLOAD/Contents/Info.plist")"
EXPECTED_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PAYLOAD/Contents/Info.plist")"
LEGACY_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$LEGACY_BRIDGE/Contents/Info.plist")"
LEGACY_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$LEGACY_BRIDGE/Contents/Info.plist")"
[[ "$EXPECTED_BUNDLE_ID" == "com.yiyu.thinktank.strict" ]] || { echo "软件身份错误：$EXPECTED_BUNDLE_ID" >&2; exit 5; }
[[ "$LEGACY_BUNDLE_ID" == "$EXPECTED_BUNDLE_ID" ]] || { echo "兼容载荷软件身份错误：$LEGACY_BUNDLE_ID" >&2; exit 6; }
[[ "$LEGACY_VERSION" == "$EXPECTED_VERSION" ]] || { echo "兼容载荷版本错误：$LEGACY_VERSION" >&2; exit 7; }

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

# 精确覆盖旧版 0.29.4 更新器的固定读取契约：根目录必须能直接读取同名 App 的
# Info.plist、签名、Bundle ID 和版本。这里在隔离目录验证，不触碰本机安装。
/usr/bin/ditto "$LEGACY_BRIDGE" "$LEGACY_TARGET"
[[ -f "$LEGACY_TARGET/Contents/Info.plist" ]] || { echo "0.29.4 兼容路径无法读取 Info.plist。" >&2; exit 10; }
[[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$LEGACY_TARGET/Contents/Info.plist")" == "$EXPECTED_BUNDLE_ID" ]] || exit 11
[[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$LEGACY_TARGET/Contents/Info.plist")" == "$EXPECTED_VERSION" ]] || exit 12
/usr/bin/codesign --verify --deep --strict "$LEGACY_TARGET"
echo "覆盖安装验证通过：0.0.0-verification -> $ACTUAL_VERSION"
echo "0.29.4 更新兼容协议验证通过：根目录固定 App -> $LEGACY_VERSION"
