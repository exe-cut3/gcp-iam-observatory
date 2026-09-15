#!/bin/sh
set -eu

mkdir -p "${OUTPUT_DIR}"
cp web/index.html web/app.js web/style.css "${OUTPUT_DIR}/"

if [ -n "${INDEX_URL:-}" ]; then
  # Normal mode: the daily GitHub workflow builds the index and this container
  # only downloads it. A failed sync keeps serving the index already in the volume.
  echo "==> syncing published index from ${INDEX_URL}"
  python -m indexer.sync --url "${INDEX_URL}" --dist "${OUTPUT_DIR}" || echo "!! sync failed; serving the existing index"
  (
    while true; do
      sleep "${SYNC_INTERVAL_SECONDS:-21600}"
      python -m indexer.sync --url "${INDEX_URL}" --dist "${OUTPUT_DIR}" || echo "!! sync failed; keeping the current index"
    done
  ) &
else
  # Development mode: build from a local collector clone.
  if [ ! -d "${COLLECTOR_REPO}/.git" ]; then
    echo "error: ${COLLECTOR_REPO} is not a git repository."
    echo "       mount your gcp-permissions-checker clone there, or set INDEX_URL (see docker-compose.yml)."
    exit 1
  fi
  echo "==> building index from ${COLLECTOR_REPO}"
  python -m indexer.build_index \
    --collector-repo "${COLLECTOR_REPO}" \
    --output-dir "${OUTPUT_DIR}" \
    --cache-dir "${CACHE_DIR}" \
    ${INDEXER_ARGS:-}
fi

echo "==> serving ${OUTPUT_DIR} on port ${PORT}"
exec python -m http.server "${PORT}" --directory "${OUTPUT_DIR}" --bind 0.0.0.0
