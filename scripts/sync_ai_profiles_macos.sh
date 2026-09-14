#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
declare -a pairs=(
  "$ROOT_DIR/config/hermes-monitoring.yaml:$ROOT_DIR/artifacts/hermes_home/config.yaml"
  "$ROOT_DIR/config/hermes-maintenance.yaml:$ROOT_DIR/artifacts/hermes_maintenance_home/config.yaml"
  "$ROOT_DIR/config/hermes-ouroboros-llm.yaml:$ROOT_DIR/artifacts/hermes_ouroboros_llm_home/config.yaml"
)
for pair in "${pairs[@]}"; do
  source_path="${pair%%:*}"
  target_path="${pair#*:}"
  mkdir -p "$(dirname "$target_path")"
  install -m 600 "$source_path" "$target_path"
done
echo 'AI_PROFILES_SYNCED'
