#!/usr/bin/env bash
set -euo pipefail

CHECK_ONLY=false
START_DOCKER=true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) CHECK_ONLY=true ;;
    --no-docker-start) START_DOCKER=false ;;
    *) echo "Unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done

if [[ "$(uname -s)" != Darwin ]]; then
  echo "This installer is only for macOS." >&2
  exit 1
fi

load_brew() {
  if command -v brew >/dev/null 2>&1; then
    return
  fi
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

load_brew
if ! command -v brew >/dev/null 2>&1; then
  if $CHECK_ONLY; then
    echo "MISSING Homebrew"
    exit 1
  fi
  installer_file="$(mktemp)"
  trap 'rm -f "$installer_file"' EXIT
  curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer_file"
  /bin/bash -n "$installer_file"
  /bin/bash "$installer_file"
  load_brew
fi

export HOMEBREW_NO_AUTO_UPDATE=1

install_formula_if_missing() {
  formula="$1"
  command_name="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    echo "OK $formula"
  elif $CHECK_ONLY; then
    echo "MISSING $formula"
    return 1
  else
    brew install "$formula"
  fi
}

install_cask_if_missing() {
  cask="$1"
  app_path="$2"
  if [[ -d "$app_path" ]]; then
    echo "OK $cask"
  elif $CHECK_ONLY; then
    echo "MISSING $cask"
    return 1
  else
    brew install --cask "$cask"
  fi
}

install_formula_if_missing python@3.12 python3.12
install_formula_if_missing git git
install_cask_if_missing google-chrome '/Applications/Google Chrome.app'
if docker info >/dev/null 2>&1; then
  echo "OK compatible Docker runtime"
elif [[ -d '/Applications/Docker.app' ]]; then
  echo "OK docker-desktop"
elif $CHECK_ONLY; then
  echo "MISSING docker-desktop or a running compatible Docker runtime"
  exit 1
else
  brew install --cask docker-desktop
fi

python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
git --version

if ! docker info >/dev/null 2>&1; then
  if $CHECK_ONLY || ! $START_DOCKER; then
    echo "MISSING running Docker daemon"
    exit 1
  fi
  open -gja Docker
  echo "Waiting for Docker Desktop..."
  docker_ready=false
  for _attempt in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then
      docker_ready=true
      break
    fi
    sleep 2
  done
  if ! $docker_ready; then
    echo "Docker Desktop did not become ready. Open it and accept its first-run terms." >&2
    exit 1
  fi
fi

if docker compose version >/dev/null 2>&1; then
  docker compose version
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose version
else
  echo "Docker Compose is not available." >&2
  exit 1
fi

echo "MACOS_PREREQUISITES_OK"
