#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

IDENTITY="$(bash scripts/resolve_mac_codesign_identity.sh)"
export MAC_CODESIGN_IDENTITY="$IDENTITY"
BUILDER_IDENTITY="${IDENTITY#Developer ID Application: }"

echo "使用签名：$IDENTITY"
npx electron-builder --mac zip --arm64 \
  -c.electronDist=node_modules/electron/dist \
  -c.mac.identity="$BUILDER_IDENTITY" \
  -c.mac.notarize=false

PAYLOAD_APP="$ROOT_DIR/dist/mac-arm64/益语智库AI（新版）.app"
OUTPUT_DMG="$(bash scripts/build_mac_update_installer.sh "$PAYLOAD_APP")"
bash scripts/verify_mac_update_installer.sh "$OUTPUT_DMG"
echo "$OUTPUT_DMG"
