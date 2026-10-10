#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
(cd api && ruff check .)
ruff check ci
npm run lint:md
npm --prefix ui run lint -- --no-fix
