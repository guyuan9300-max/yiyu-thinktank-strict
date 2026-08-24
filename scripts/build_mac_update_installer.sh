#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PAYLOAD_APP="${1:-$ROOT_DIR/dist/mac-arm64/益语智库AI（新版）.app}"
REQUESTED_OUTPUT_DMG="${2:-}"
IDENTITY="${MAC_CODESIGN_IDENTITY:-$(bash "$ROOT_DIR/scripts/resolve_mac_codesign_identity.sh")}"
SOURCE_DIR="$ROOT_DIR/build-resources/mac-update-installer"

if [[ ! -d "$PAYLOAD_APP" ]]; then
  echo "Missing payload app: $PAYLOAD_APP" >&2
  exit 2
fi

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PAYLOAD_APP/Contents/Info.plist")"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PAYLOAD_APP/Contents/Info.plist")"
OUTPUT_DMG="${REQUESTED_OUTPUT_DMG:-$ROOT_DIR/dist/yiyu-thinktank-strict-$VERSION-arm64.dmg}"
if [[ "$BUNDLE_ID" != "com.yiyu.thinktank.strict" ]]; then
  echo "Unexpected payload bundle id: $BUNDLE_ID" >&2
  exit 3
fi
/usr/bin/codesign --verify --deep --strict "$PAYLOAD_APP"

WORK_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/yiyu-update-installer.XXXXXX")"
VERIFY_MOUNT="$WORK_DIR/verify-mounted"
VERIFY_MOUNTED=0
cleanup() {
  if [[ "$VERIFY_MOUNTED" == "1" ]]; then
    /usr/bin/hdiutil detach "$VERIFY_MOUNT" -force >/dev/null 2>&1 || true
  fi
  /bin/rm -rf "$WORK_DIR"
}
trap cleanup EXIT
INSTALLER_APP="$WORK_DIR/安装或更新益语智库AI.app"
CONTENTS="$INSTALLER_APP/Contents"
/bin/mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources/Payload"
/bin/cp "$SOURCE_DIR/Info.plist" "$CONTENTS/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$CONTENTS/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${VERSION//./}" "$CONTENTS/Info.plist"
/bin/cp "$ROOT_DIR/build-resources/icon.icns" "$CONTENTS/Resources/icon.icns"
/usr/bin/xcrun swiftc -O -framework AppKit "$SOURCE_DIR/UpdateInstaller.swift" -o "$CONTENTS/MacOS/安装或更新益语智库AI"
/usr/bin/ditto "$PAYLOAD_APP" "$CONTENTS/Resources/Payload/益语智库AI（新版）.app"
/usr/bin/codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$INSTALLER_APP"
/usr/bin/codesign --verify --deep --strict "$INSTALLER_APP"

README="$WORK_DIR/请双击“安装或更新益语智库AI”.txt"
/usr/bin/printf '%s\n' \
  '1. 双击“安装或更新益语智库AI”。' \
  '2. 点击“安装并打开”。' \
  '' \
  '安装程序会自动退出旧版、原位替换扩展坞实际使用的软件并打开新版；无需拖拽或手动删除旧版。' > "$README"
/bin/rm -f "$OUTPUT_DMG"
/usr/bin/hdiutil create -quiet -volname "益语智库AI（新版） $VERSION" -srcfolder "$WORK_DIR" -format UDZO "$OUTPUT_DMG"
/usr/bin/codesign --force --timestamp --sign "$IDENTITY" "$OUTPUT_DMG"
/usr/bin/codesign --verify "$OUTPUT_DMG"

# Every release must remain a migration installer, never a legacy drag-to-Applications DMG.
# Refuse to emit an artifact when its structure, nested payload identity, version, or signature drifts.
/bin/mkdir -p "$VERIFY_MOUNT"
/usr/bin/hdiutil attach -quiet "$OUTPUT_DMG" -nobrowse -readonly -mountpoint "$VERIFY_MOUNT"
VERIFY_MOUNTED=1
VERIFY_INSTALLER="$VERIFY_MOUNT/安装或更新益语智库AI.app"
VERIFY_PAYLOAD="$VERIFY_INSTALLER/Contents/Resources/Payload/益语智库AI（新版）.app"
[[ -d "$VERIFY_INSTALLER" ]] || { echo "Final DMG is missing the update installer" >&2; exit 4; }
[[ -d "$VERIFY_PAYLOAD" ]] || { echo "Final DMG is missing the nested product payload" >&2; exit 5; }
[[ ! -e "$VERIFY_MOUNT/益语智库AI（新版）.app" ]] || { echo "Legacy direct-app DMG layout is forbidden" >&2; exit 6; }
[[ ! -L "$VERIFY_MOUNT/Applications" ]] || { echo "Legacy drag-to-Applications layout is forbidden" >&2; exit 7; }
/usr/bin/codesign --verify --deep --strict "$VERIFY_INSTALLER"
/usr/bin/codesign --verify --deep --strict "$VERIFY_PAYLOAD"
VERIFY_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$VERIFY_PAYLOAD/Contents/Info.plist")"
VERIFY_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$VERIFY_PAYLOAD/Contents/Info.plist")"
[[ "$VERIFY_BUNDLE_ID" == "$BUNDLE_ID" ]] || { echo "Final payload bundle id drifted: $VERIFY_BUNDLE_ID" >&2; exit 8; }
[[ "$VERIFY_VERSION" == "$VERSION" ]] || { echo "Final payload version drifted: $VERIFY_VERSION" >&2; exit 9; }
/usr/bin/hdiutil detach -quiet "$VERIFY_MOUNT"
VERIFY_MOUNTED=0
echo "$OUTPUT_DMG"
