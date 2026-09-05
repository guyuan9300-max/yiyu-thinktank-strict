#!/usr/bin/env bash
set -euo pipefail

artifact=""
host=""
expected_sha=""
identity_file=""
release_base="/opt/yiyu-strict-cloud"
service_name="yiyu-strict-cloud.service"
runtime_python_override=""

while (($#)); do
  case "$1" in
    --artifact) artifact="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --expected-sha) expected_sha="$2"; shift 2 ;;
    --identity) identity_file="$2"; shift 2 ;;
    --release-base) release_base="$2"; shift 2 ;;
    --service) service_name="$2"; shift 2 ;;
    --runtime-python) runtime_python_override="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$artifact" || -z "$host" || -z "$expected_sha" ]]; then
  echo "usage: $0 --artifact FILE --host USER@HOST --expected-sha SHA [--identity FILE] [--runtime-python REMOTE_PATH]" >&2
  exit 2
fi
if [[ ! -f "$artifact" || ! "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "artifact or expected SHA is invalid" >&2
  exit 2
fi
if [[ -n "$runtime_python_override" \
    && ! "$runtime_python_override" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "runtime Python override must be a safe absolute path" >&2
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
  "$release_base" "$service_name" "$expected_sha" "$remote_archive" \
  "$runtime_python_override" <<'REMOTE_SCRIPT'
set -euo pipefail
release_base="$1"
service_name="$2"
expected_sha="$3"
remote_archive="$4"
runtime_python_override="${5:-}"
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

if [[ -n "$runtime_python_override" ]]; then
  runtime_python="$runtime_python_override"
else
  service_exec_start="$(systemctl show "$service_name" --property=ExecStart --value)"
  runtime_python="$(printf '%s\n' "$service_exec_start" | sed -n \
    's/.*[ {]path=\([^ ;}]*\).*/\1/p')"
fi
if [[ "$runtime_python" != /* || ! -x "$runtime_python" ]]; then
  echo "runtime Python is not an executable absolute path: $runtime_python" >&2
  exit 3
fi
runtime_python="$(readlink -f -- "$runtime_python")"
if [[ -z "$runtime_python" || ! -x "$runtime_python" ]] \
    || ! "$runtime_python" -c \
      'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "runtime Python failed validation: $runtime_python" >&2
  exit 3
fi
"$runtime_python" "$release_dir/scripts/strict_cloud_release.py" \
  verify "$release_dir" --expected-sha "$expected_sha"
chown -R yiyu:yiyu "$release_dir"

set -a
. /etc/yiyu-strict-cloud/production.env
set +a
: "${YIYU_STRICT_CLOUD_DATA_DIR:?YIYU_STRICT_CLOUD_DATA_DIR is required}"
: "${YIYU_STRICT_CLOUD_INSTANCE_ID:?YIYU_STRICT_CLOUD_INSTANCE_ID is required}"
: "${YIYU_STRICT_CLOUD_HOST:?YIYU_STRICT_CLOUD_HOST is required}"
: "${YIYU_STRICT_CLOUD_PORT:?YIYU_STRICT_CLOUD_PORT is required}"
install -d -o yiyu -g yiyu "$YIYU_STRICT_CLOUD_DATA_DIR"
runuser -u yiyu -- env PYTHONPATH="$release_dir" \
  "$runtime_python" -m cloud_backend.app.provisioning \
  --database "$YIYU_STRICT_CLOUD_DATA_DIR/strict-cloud.db" \
  --cloud-instance-id "$YIYU_STRICT_CLOUD_INSTANCE_ID"

ln -sfn "$release_dir" "$next_link"
mv -Tf "$next_link" "$current_link"
if ! systemctl restart "$service_name"; then
  ln -sfn "$previous_release" "$next_link"
  mv -Tf "$next_link" "$current_link"
  systemctl restart "$service_name" || \
    echo "service restart failed; automatic rollback restart also failed" >&2
  exit 4
fi

restore_previous_release() {
  ln -sfn "$previous_release" "$next_link" || return 1
  mv -Tf "$next_link" "$current_link" || return 1
  systemctl restart "$service_name"
}

rollback_after_gate_failure() {
  local reason="$1"
  if restore_previous_release; then
    echo "$reason; previous release restored" >&2
  else
    echo "$reason; automatic rollback failed, operator action required" >&2
  fi
}

probe_host="$YIYU_STRICT_CLOUD_HOST"
if [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "::" || "$probe_host" == "[::]" ]]; then
  probe_host="127.0.0.1"
fi
if [[ "$probe_host" == *:* && "$probe_host" != \[*\] ]]; then
  probe_authority="[$probe_host]:$YIYU_STRICT_CLOUD_PORT"
else
  probe_authority="$probe_host:$YIYU_STRICT_CLOUD_PORT"
fi
probe_base_url="http://$probe_authority"

healthy=0
for _ in $(seq 1 30); do
  if health_json="$(curl -fsS \
      --connect-timeout 2 --max-time 5 \
      "$probe_base_url/api/v2/health" 2>/dev/null)" \
      && printf '%s' "$health_json" | "$runtime_python" -c \
      'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("status") == "ready" else 1)' 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" != 1 ]]; then
  rollback_after_gate_failure "health check failed"
  exit 5
fi

route_probe_response="$(mktemp)"
trap 'rm -f "$route_probe_response"' EXIT
route_ready=0
for _ in $(seq 1 10); do
  route_status="$(curl -sS \
    --connect-timeout 2 --max-time 5 \
    --output "$route_probe_response" \
    --write-out '%{http_code}' \
    --request POST \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --data-binary '{"question":"__route_probe__","viewerName":"route-probe"}' \
    "$probe_base_url/api/v2/ui/tasks/schedule-assistant/ask" 2>/dev/null || true)"
  if [[ "$route_status" == "401" ]] \
      && "$runtime_python" - "$route_probe_response" <<'PYTHON'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as response_file:
        payload = json.load(response_file)
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

error = payload.get("error")
raise SystemExit(
    0
    if isinstance(error, dict) and error.get("code") == "authorization_required"
    else 1
)
PYTHON
  then
    route_ready=1
    break
  fi
  sleep 1
done
rm -f "$route_probe_response"
trap - EXIT
if [[ "$route_ready" != 1 ]]; then
  rollback_after_gate_failure \
    "schedule-assistant route gate failed (expected HTTP 401 authorization_required)"
  exit 6
fi

rm -f "$remote_archive"
echo "deployed $release_id from $expected_sha; previous=$previous_release"
REMOTE_SCRIPT
