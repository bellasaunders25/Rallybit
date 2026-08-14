#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

if ! curl --fail --silent --show-error --head --max-time 5 http://127.0.0.1/ >/dev/null; then
  echo "Rallybit's local Apache site is not responding on port 80." >&2
  exit 1
fi

backup_dir="/var/backups/rallybit"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
tailscale funnel status --json >"$backup_dir/tailscale-funnel-$timestamp.json"

# Remove any previous handler on public port 443 before installing Rallybit's
# HTTPS reverse proxy. Other configured Funnel ports are preserved. Supporting
# both modes makes this safe to rerun after Rallybit is already configured.
tailscale funnel --tcp=443 off >/dev/null 2>&1 || true
tailscale funnel --https=443 off >/dev/null 2>&1 || true
tailscale funnel --bg --yes --https=443 http://127.0.0.1:80

status="$(tailscale funnel status)"
if ! grep -Fq "proxy http://127.0.0.1:80" <<<"$status"; then
  echo "Tailscale did not retain Rallybit's HTTPS proxy." >&2
  exit 1
fi

printf '%s\n' "$status"
printf 'Previous Funnel configuration: %s\n' "$backup_dir/tailscale-funnel-$timestamp.json"
