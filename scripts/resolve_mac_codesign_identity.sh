#!/bin/bash
set -euo pipefail

if [[ -n "${MAC_CODESIGN_IDENTITY:-}" ]]; then
  IDENTITY="$MAC_CODESIGN_IDENTITY"
else
  IDENTITY="$({ /usr/bin/security find-identity -v -p codesigning 2>/dev/null || true; } \
    | /usr/bin/sed -n 's/^.*"\(Developer ID Application: [^"]*\)".*$/\1/p' \
    | /usr/bin/head -n 1)"
fi

if [[ -z "$IDENTITY" ]]; then
  echo "未在当前 Mac 钥匙串找到有效的 Developer ID Application 签名证书。" >&2
  echo "请先把 Apple Developer 导出的签名证书（含私钥）导入“登录”钥匙串，再重试。" >&2
  exit 2
fi

if ! /usr/bin/security find-identity -v -p codesigning | /usr/bin/grep -Fq "\"$IDENTITY\""; then
  echo "指定的签名身份当前无效或不含私钥：$IDENTITY" >&2
  exit 3
fi

echo "$IDENTITY"
