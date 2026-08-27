#!/usr/bin/env bash
set -euo pipefail

artifact=""
host=""
expected_sha=""
identity_file=""
release_base="/opt/yiyu-strict-cloud"
service_name="yiyu-strict-cloud.service"

while (($#)); do
  case "$1" in
    --artifact) artifact="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --expected-sha) expected_sha="$2"; shift 2 ;;
    --identity) identity_file="$2"; shift 2 ;;
    --release-base) release_base="$2"; shift 2 ;;
    --service) service_name="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$artifact" || -z "$host" || -z "$expected_sha" ]]; then
  echo "usage: $0 --artifact FILE --host USER@HOST --expected-sha SHA [--identity FILE]" >&2
  exit 2
fi
if [[ ! -f "$artifact" || ! "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "artifact or expected SHA is invalid" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
uv run --directory "$repo_root" python scripts/strict_cloud_release.py \
  verify "$artifact" --expected-sha "$expected_sha"

ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
scp_args=(-o BatchMode=yes -o ConnectTimeout=10)
if [[ -n "$identity_file" ]]; then
  ssh_args+=(-i "$identity_file")
  scp_args+=(-i "$identity_file")
fi

archive_name="strict-${expected_sha:0:12}.tar.gz"
remote_archive="$release_base/incoming/$archive_name"
ssh "${ssh_args[@]}" "$host" mkdir -p "$release_base/incoming" "$release_base/releases"
scp "${scp_args[@]}" "$artifact" "$host:$remote_archive"

ssh "${ssh_args[@]}" "$host" bash -s -- \
  "$release_base" "$service_name" "$expected_sha" "$remote_archive" <<'REMOTE_SCRIPT'
set -euo pipefail
release_base="$1"
service_name="$2"
expected_sha="$3"
remote_archive="$4"
release_id="strict-${expected_sha:0:12}"
release_dir="$release_base/releases/$release_id"
current_link="$release_base/current"
next_link="$release_base/current.next"
previous_release="$(readlink -f "$current_link")"

if [[ -e "$release_dir" ]]; then
  echo "release target already exists: $release_dir" >&2
  exit 3
fi
mkdir -p "$release_dir"
tar -xzf "$remote_archive" -C "$release_dir"

runtime_python="$(readlink -f "$release_base/venvs"/*/bin/python | head -n 1)"
"$runtime_python" "$release_dir/scripts/strict_cloud_release.py" \
  verify "$release_dir" --expected-sha "$expected_sha"
chown -R yiyu:yiyu "$release_dir"

ln -sfn "$release_dir" "$next_link"
mv -Tf "$next_link" "$current_link"
if ! systemctl restart "$service_name"; then
  ln -sfn "$previous_release" "$next_link"
  mv -Tf "$next_link" "$current_link"
  systemctl restart "$service_name"
  exit 4
fi

set -a
. /etc/yiyu-strict-cloud/production.env
set +a
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS "http://${YIYU_STRICT_CLOUD_HOST}:${YIYU_STRICT_CLOUD_PORT}/api/v2/health" \
      | "$runtime_python" -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("status") == "ready" else 1)'; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" != 1 ]]; then
  ln -sfn "$previous_release" "$next_link"
  mv -Tf "$next_link" "$current_link"
  systemctl restart "$service_name"
  echo "health check failed; previous release restored" >&2
  exit 5
fi
rm -f "$remote_archive"
echo "deployed $release_id from $expected_sha; previous=$previous_release"
REMOTE_SCRIPT
