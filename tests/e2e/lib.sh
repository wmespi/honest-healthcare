# Helpers the e2e tests source.

# serve_start serves a folder under data-test/ over HTTP from the serving
# container, so the etl container downloads from it the way it does from Anthem
# (http://serving:<port>/<file>). Python's server sends each file's mtime as its
# Last-Modified, which parse records as the update of the file it read.

# serve_start <folder under data-test/> <port>
serve_start() {
  docker compose exec -d serving sh -c \
    "cd /app/data-test/$1 && echo \$\$ > /tmp/e2e_http_$2.pid && exec python3 -m http.server $2"
  for _ in $(seq 1 50); do
    if docker compose exec -T serving python3 -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$2/')" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  echo "FAIL: the e2e file server on port $2 didn't start"
  return 1
}

# serve_stop <port>
serve_stop() {
  docker compose exec -T serving sh -c \
    "kill \$(cat /tmp/e2e_http_$1.pid) 2>/dev/null; rm -f /tmp/e2e_http_$1.pid" >/dev/null 2>&1 || true
}

# store_mark notes the time a test starts, and store_untouched fails if the run
# wrote anything under data/ since: a test run writes only data-test/
# (Critical Rule 4). Both look through the etl container, which mounts the store.
store_mark() {
  docker compose exec -T etl sh -c "mkdir -p /app/data-test && touch /app/data-test/.e2e_start"
}

store_untouched() {
  local changed
  changed=$(docker compose exec -T etl sh -c \
    "[ -d /app/data ] && find /app/data -newer /app/data-test/.e2e_start -type f | head -5" || true)
  if [ -n "$changed" ]; then
    echo "FAIL: the test run wrote under data/:"
    echo "$changed"
    return 1
  fi
}
