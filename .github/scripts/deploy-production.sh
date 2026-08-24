#!/usr/bin/env bash
set -euo pipefail
set +x

target_commit="${1:-}"
if [[ ! "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: $0 <40-character-git-commit>" >&2
  exit 1
fi

required_environment=(
  VULTR_API_KEY
  VULTR_FIREWALL_GROUP_ID
  CIVICLOOP_DEPLOY_SSH_PRIVATE_KEY
  CIVICLOOP_SSH_KNOWN_HOSTS
  CIVICLOOP_DEPLOY_HOST
  CIVICLOOP_DEPLOY_USER
)
for variable_name in "${required_environment[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Missing required deployment setting: $variable_name" >&2
    exit 1
  fi
done

runtime_dir="$(mktemp -d)"
private_key_file="$runtime_dir/deploy-key"
known_hosts_file="$runtime_dir/known_hosts"
firewall_response_file="$runtime_dir/firewall-response.json"
firewall_rule_id=""

cleanup() {
  local result=$?
  trap - EXIT

  if [[ -n "$firewall_rule_id" ]]; then
    if ! curl --fail --silent --show-error \
      --request DELETE \
      --header "Authorization: Bearer $VULTR_API_KEY" \
      "https://api.vultr.com/v2/firewalls/$VULTR_FIREWALL_GROUP_ID/rules/$firewall_rule_id"; then
      echo "WARNING: Could not remove temporary Vultr firewall rule $firewall_rule_id." >&2
    fi
  fi

  rm -f "$private_key_file" "$known_hosts_file" "$firewall_response_file"
  rmdir "$runtime_dir" 2>/dev/null || true
  exit "$result"
}
trap cleanup EXIT

printf '%s\n' "$CIVICLOOP_DEPLOY_SSH_PRIVATE_KEY" | tr -d '\r' >"$private_key_file"
printf '%s\n' "$CIVICLOOP_SSH_KNOWN_HOSTS" | tr -d '\r' >"$known_hosts_file"
chmod 0600 "$private_key_file" "$known_hosts_file"
unset CIVICLOOP_DEPLOY_SSH_PRIVATE_KEY CIVICLOOP_SSH_KNOWN_HOSTS

runner_ip="$(curl --fail --silent --show-error --retry 3 https://api.ipify.org)"
python3 - "$runner_ip" <<'PY'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
if address.version != 4:
    raise SystemExit("The GitHub runner did not report an IPv4 address.")
PY

rule_note="civicloop-github-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}"
firewall_payload="$(printf '{"ip_type":"v4","protocol":"tcp","port":"22","subnet":"%s","subnet_size":32,"notes":"%s"}' "$runner_ip" "$rule_note")"
http_status="$(
  curl --silent --show-error     --output "$firewall_response_file"     --write-out '%{http_code}'     --request POST     --header "Authorization: Bearer $VULTR_API_KEY"     --header "Content-Type: application/json"     --data "$firewall_payload"     "https://api.vultr.com/v2/firewalls/$VULTR_FIREWALL_GROUP_ID/rules"
)"
if [[ ! "$http_status" =~ ^20[0-9]$ ]]; then
  echo "Vultr rejected the temporary SSH firewall rule (HTTP $http_status)." >&2
  exit 1
fi

firewall_rule_id="$(
  python3 - "$firewall_response_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    payload = json.load(response)
rule_id = payload.get("firewall_rule", {}).get("id")
if not isinstance(rule_id, int):
    raise SystemExit("Vultr did not return a firewall rule ID.")
print(rule_id)
PY
)"

ssh_options=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$known_hosts_file"
  -i "$private_key_file"
)

for attempt in {1..12}; do
  if timeout 5 bash -c "exec 3<>/dev/tcp/$CIVICLOOP_DEPLOY_HOST/22"; then
    break
  fi
  if [[ "$attempt" -eq 12 ]]; then
    echo "SSH did not become reachable after adding the temporary firewall rule." >&2
    exit 1
  fi
  sleep 5
done

ssh "${ssh_options[@]}"   "$CIVICLOOP_DEPLOY_USER@$CIVICLOOP_DEPLOY_HOST"   deploy "$target_commit"

curl --fail --silent --show-error --location --max-time 20   "https://$CIVICLOOP_DEPLOY_HOST/api/v1/health/ready" >/dev/null
echo "Verified CivicLoop production readiness at commit $target_commit"
