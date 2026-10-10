#!/bin/sh
set -eu

state=/run/local-state
routes=/tmp/local-routes
mkdir -p "$routes"
printf '    location = /bookstack { return 404; }\n    location ^~ /bookstack/ { return 404; }\n' > "$routes/bookstack.conf"
printf '    location = /git { return 404; }\n    location ^~ /git/ { return 404; }\n' > "$routes/gitea.conf"
printf '    location = /ci { return 404; }\n    location ^~ /ci/ { return 404; }\n' > "$routes/ci.conf"
nginx -g 'daemon off;' &
nginx_pid=$!
trap 'kill "$nginx_pid" 2>/dev/null || true; wait "$nginx_pid" 2>/dev/null || true' INT TERM EXIT

while kill -0 "$nginx_pid" 2>/dev/null; do
  generation=$(cat "$state/bootstrap-generation" 2>/dev/null || true)
  case "$generation" in
    ""|*[!a-f0-9]*) generation=invalid ;;
  esac
  printf '%s' "$generation" > "$routes/current-generation"
  changed=0
  for route in bookstack gitea ci; do
    enabled=0
    marker="$state/ready/$route"
    if [ "$generation" != invalid ] && [ -f "$marker" ] && [ ! -L "$marker" ]; then
      cmp -s "$routes/current-generation" "$marker" && enabled=1
    fi
    output="$routes/$route.conf"
    temporary="$output.new"
    case "$route:$enabled" in
      bookstack:1)
        cat > "$temporary" <<'EOF'
    location = /bookstack { return 308 /bookstack/; }
    location ^~ /bookstack/ { set $backend legacy-bookstack:80; rewrite ^/bookstack/?(.*)$ /$1 break; proxy_pass http://$backend; }
EOF
        ;;
      gitea:1)
        cat > "$temporary" <<'EOF'
    location = /git { return 308 /git/; }
    location ^~ /git/ { set $backend legacy-gitea:3000; rewrite ^/git/?(.*)$ /$1 break; proxy_pass http://$backend; }
EOF
        ;;
      ci:1)
        sed 's#proxy_pass http://127.0.0.1:8000;#set $ci_backend ci-server:8000; proxy_pass http://$ci_backend;#g' \
          /usr/local/share/woodpecker.conf > "$temporary"
        ;;
      bookstack:0)
        printf '    location = /bookstack { return 404; }\n    location ^~ /bookstack/ { return 404; }\n' > "$temporary"
        ;;
      gitea:0)
        printf '    location = /git { return 404; }\n    location ^~ /git/ { return 404; }\n' > "$temporary"
        ;;
      ci:0)
        printf '    location = /ci { return 404; }\n    location ^~ /ci/ { return 404; }\n' > "$temporary"
        ;;
    esac
    chmod 0644 "$temporary"
    if ! cmp -s "$temporary" "$output" 2>/dev/null; then
      mv "$temporary" "$output"
      changed=1
    else
      rm -f "$temporary"
    fi
  done
  if [ "$changed" -eq 1 ]; then
    if nginx -t; then
      nginx -s reload
    else
      printf 'Generated local route configuration failed validation.\n' >&2
    fi
  fi
  sleep 2
done
