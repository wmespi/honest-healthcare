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

# public_ids prints the last id public.index_files handed out. A test run must
# never move it (Critical Rule 4).
public_ids() {
  docker compose exec -T db psql -U postgres -d honest_healthcare -qtA \
    -c "SELECT last_value FROM public.index_files_id_seq;"
}
