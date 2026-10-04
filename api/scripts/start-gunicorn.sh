#!/bin/sh
set -eu

PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/python-demo-prometheus-${HOSTNAME:-$$}}"

case "$PROMETHEUS_MULTIPROC_DIR" in
  /tmp/python-demo-prometheus-*) ;;
  *) echo "PROMETHEUS_MULTIPROC_DIR must be a private /tmp/python-demo-prometheus-* directory" >&2; exit 2 ;;
esac
if [ -L "$PROMETHEUS_MULTIPROC_DIR" ]; then
  echo "PROMETHEUS_MULTIPROC_DIR must not be a symbolic link" >&2
  exit 2
fi

resolved_dir=$(realpath -m -- "$PROMETHEUS_MULTIPROC_DIR")
case "$resolved_dir" in
  /tmp/python-demo-prometheus-*) ;;
  *) echo "PROMETHEUS_MULTIPROC_DIR resolves outside its private runtime directory" >&2; exit 2 ;;
esac
case "${resolved_dir#/tmp/}" in
  */*) echo "PROMETHEUS_MULTIPROC_DIR must be a direct child of /tmp" >&2; exit 2 ;;
esac

current_uid=$(id -u)
owner_marker="$resolved_dir/.python-demo-prometheus-owner"
marker_content="python-demo-prometheus-v1:${current_uid}:${resolved_dir}"

verify_owned_directory() {
  if [ ! -d "$resolved_dir" ] || [ -L "$resolved_dir" ]; then
    echo "PROMETHEUS_MULTIPROC_DIR must be a real private directory" >&2
    exit 2
  fi

  directory_metadata=$(stat -c '%u %a' -- "$resolved_dir")
  directory_owner=${directory_metadata%% *}
  directory_mode=${directory_metadata#* }
  if [ "$directory_owner" != "$current_uid" ] || [ "$directory_mode" != "700" ]; then
    echo "PROMETHEUS_MULTIPROC_DIR must be owned by this user with mode 0700" >&2
    exit 2
  fi

  if [ ! -f "$owner_marker" ] || [ -L "$owner_marker" ]; then
    echo "PROMETHEUS_MULTIPROC_DIR owner marker is missing or invalid" >&2
    exit 2
  fi
  marker_metadata=$(stat -c '%u %a' -- "$owner_marker")
  marker_owner=${marker_metadata%% *}
  marker_mode=${marker_metadata#* }
  if [ "$marker_owner" != "$current_uid" ] || [ "$marker_mode" != "600" ]; then
    echo "PROMETHEUS_MULTIPROC_DIR owner marker must be private and owned by this user" >&2
    exit 2
  fi
  if [ "$(cat -- "$owner_marker")" != "$marker_content" ]; then
    echo "PROMETHEUS_MULTIPROC_DIR owner marker does not match this instance" >&2
    exit 2
  fi
}

if [ -e "$resolved_dir" ]; then
  verify_owned_directory
else
  if ! (umask 077 && mkdir -m 700 -- "$resolved_dir"); then
    echo "PROMETHEUS_MULTIPROC_DIR could not be exclusively created" >&2
    exit 2
  fi
  if ! (umask 077 && printf '%s\n' "$marker_content" > "$owner_marker"); then
    echo "PROMETHEUS_MULTIPROC_DIR owner marker could not be created" >&2
    exit 2
  fi
  chmod 600 -- "$owner_marker"
  verify_owned_directory
fi

find "$resolved_dir" -mindepth 1 -maxdepth 1 -type f \
  \( -name 'counter_*.db' -o -name 'histogram_*.db' -o -name 'gauge_*.db' \) -delete

PROMETHEUS_MULTIPROC_DIR="$resolved_dir"
export PROMETHEUS_MULTIPROC_DIR
exec gunicorn "$@"
