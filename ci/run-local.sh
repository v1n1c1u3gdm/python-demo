#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
reports_root="${CI_REPORTS_ROOT:-$repo_root/.ci-reports}"
commit="${CI_COMMIT_SHA:-$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || printf 'local')}"
run="${CI_RUN_ID:-$(date -u +%s%N)}"
cli_image="woodpeckerci/woodpecker-cli:v3.18.0@sha256:cba80a18e41e29500cc72b81f788986aee3fbe4c2c9c1b9a0112aa0cc019e215"
job_image="docker.io/library/python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d"
if [[ ! "$commit" =~ ^[A-Za-z0-9._-]+$ || ! "$run" =~ ^[0-9]+$ ]]; then
  printf 'Local quality run cannot start: invalid commit or execution identifier\n' >&2
  exit 2
fi
docker_bin="${CI_DOCKER_BIN:-docker}"
if ! command -v -- "$docker_bin" >/dev/null 2>&1; then
  printf 'Local quality run cannot start: Docker is required to execute the shared Woodpecker workflow\n' >&2
  exit 127
fi
if [[ -z "${CI_DOCKER_BIN:-}" && ! -S /var/run/docker.sock ]]; then
  printf 'Local quality run cannot start: Docker control socket is unavailable\n' >&2
  exit 127
fi
docker_cli_name=()
if [[ -n "${CI_DOCKER_CLI_NAME:-}" ]]; then
  if [[ ! "$CI_DOCKER_CLI_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
    printf 'Local quality run cannot start: invalid Docker CLI container name\n' >&2
    exit 2
  fi
  docker_cli_name=(--name "$CI_DOCKER_CLI_NAME")
fi
if ! mkdir -p -- "$reports_root"; then
  printf 'Cannot create report root: %s\n' "$reports_root" >&2
  exit 2
fi
report_dir=$(mktemp -d -- "$reports_root/$commit-$run.XXXXXX") || exit 2
snapshot=$(mktemp -d -- "${TMPDIR:-/tmp}/quality-workspace.XXXXXX") || exit 2
cleanup_snapshot() {
  if rm -rf -- "$snapshot" 2>/dev/null; then
    return 0
  fi
  if [[ -d "$snapshot" ]]; then
    "$docker_bin" run --rm --user 0:0 -v "$snapshot:$snapshot" \
      --entrypoint python3 "$job_image" -c \
      'import pathlib, shutil, sys; root=pathlib.Path(sys.argv[1]); [shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink() for p in root.iterdir()]' "$snapshot" >/dev/null || return 1
    rm -rf -- "$snapshot" || return 1
  fi
}
trap cleanup_snapshot EXIT

git clone --quiet --no-hardlinks --no-checkout "$repo_root" "$snapshot" || {
  printf 'Cannot copy the full Git history into the isolated workspace\n' >&2
  exit 2
}
git -C "$snapshot" remote remove origin >/dev/null 2>&1 || true
if ! git -C "$snapshot" fetch --quiet --no-tags "$repo_root" '+refs/*:refs/source/*'; then
  printf 'Cannot preserve all Git refs in the isolated workspace\n' >&2
  exit 2
fi
rm -rf -- "$snapshot/.git/hooks"
paths_file="$snapshot/.paths"
archive_file="$snapshot/.workspace.tar"
git -C "$repo_root" ls-files --cached --others --exclude-standard -z > "$paths_file" || exit 2
filtered_paths="$snapshot/.filtered-paths"
: > "$filtered_paths"
while IFS= read -r -d '' path; do
  case "$path" in
    api/logs/*) continue ;;
  esac
  IFS='/' read -r -a components <<< "$path"
  parent="$repo_root"
  for ((index = 0; index < ${#components[@]} - 1; index++)); do
    parent="$parent/${components[index]}"
    if [[ -L "$parent" ]]; then
      printf 'Cannot snapshot %s: an ancestor is a symlink: %s\n' "$path" "$parent" >&2
      exit 2
    fi
  done
  if [[ -e "$repo_root/$path" || -L "$repo_root/$path" ]]; then
    printf '%s\0' "$path" >> "$filtered_paths"
  fi
done < "$paths_file"
if ! tar -C "$repo_root" --null --files-from="$filtered_paths" -cf "$archive_file"; then
  printf 'Cannot snapshot the current source tree\n' >&2
  exit 2
fi
if ! tar -C "$snapshot" -xf "$archive_file"; then
  printf 'Cannot prepare the isolated workflow source snapshot\n' >&2
  exit 2
fi
rm -f -- "$paths_file" "$filtered_paths" "$archive_file"

printf 'Running .woodpecker/quality.yaml with Woodpecker CLI v3.18.0.\n'
printf 'Current checkout files and full Git history are copied to a temporary workspace; ignored files and api/logs are excluded. The workflow receives only that temporary workspace; reports are copied out after it exits.\n'
set +e
"$docker_bin" run --rm --user 0:0 \
  "${docker_cli_name[@]}" \
  -v "$snapshot:$snapshot" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w "$snapshot" \
  "$cli_image" exec --local --repo-path "$snapshot" --backend-engine docker \
  --pipeline-event pull_request --commit-sha "$commit" --pipeline-number "$run" \
  .woodpecker/quality.yaml
status=$?
set -e
reports_source="$snapshot/.ci-reports"
if [[ -L "$reports_source" ]]; then
  printf 'Cannot export local reports: .ci-reports is a symlink\n' >&2
  status=74
elif [[ -e "$reports_source" && ! -d "$reports_source" ]]; then
  printf 'Cannot export local reports: .ci-reports is not a directory\n' >&2
  status=74
elif [[ -d "$reports_source" ]]; then
  if ! report_symlink=$(find "$reports_source" -type l -print -quit); then
    printf 'Cannot inspect local reports for symlinks\n' >&2
    status=74
  elif [[ -n "$report_symlink" ]]; then
    printf 'Cannot export local reports: symlink found: %s\n' "$report_symlink" >&2
    status=74
  elif ! cp -a -- "$reports_source/." "$report_dir/"; then
    printf 'Cannot preserve local reports in %s\n' "$report_dir" >&2
    if (( status == 0 )); then
      status=74
    fi
  fi
fi
if ! cleanup_snapshot; then
  printf 'Cannot clean the isolated workflow snapshot: %s\n' "$snapshot" >&2
  if (( status == 0 )); then
    status=74
  fi
fi
trap - EXIT
printf 'Local reports: %s\n' "$report_dir"
run_reports="$report_dir/$commit-$run"
if [[ -d "$run_reports" ]]; then
  if ! python3 -m ci.collect_results "$run_reports"; then
    if (( status == 0 )); then
      status=1
    fi
  fi
  if ! python3 -m ci.retention "$reports_root"; then
    printf 'Cannot enforce local report retention under %s\n' "$reports_root" >&2
    if (( status == 0 )); then
      status=1
    fi
  fi
else
  printf 'No reports were produced for execution %s/%s\n' "$commit" "$run" >&2
  if (( status == 0 )); then
    status=1
  fi
fi
exit "$status"
