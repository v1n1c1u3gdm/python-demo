#!/usr/bin/env bash
set -euo pipefail
if ! command -v bandit >/dev/null; then printf 'Bandit 1.9.4 is required.\n' >&2; exit 127; fi
version="$(bandit --version 2>&1 | head -n1)"
[[ "$version" == *"1.9.4"* ]] || { printf 'Bandit 1.9.4 required; found %s.\n' "$version" >&2; exit 2; }
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
(cd api && ruff check .)
ruff check ci
bandit -r api -x api/tests -ll -ii
