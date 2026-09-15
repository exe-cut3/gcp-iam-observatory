#!/bin/sh
set -eu

echo "==> building index from ${COLLECTOR_REPO}"

if [ ! -d "${COLLECTOR_REPO}/.git" ]; then
  echo "error: ${COLLECTOR_REPO} is not a git repository."
  echo "       mount your gcp-permissions-checker clone there (see docker-compose.yml)."
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

# Static assets are copied rather than served from web/ so the published
# directory is exactly what a deploy would upload.
cp web/index.html web/app.js web/style.css "${OUTPUT_DIR}/"

python -m indexer.build_index \
  --collector-repo "${COLLECTOR_REPO}" \
  --output-dir "${OUTPUT_DIR}" \
  --cache-dir "${CACHE_DIR}" \
  ${INDEXER_ARGS:-}

echo "==> serving ${OUTPUT_DIR} on port ${PORT}"
exec python -m http.server "${PORT}" --directory "${OUTPUT_DIR}" --bind 0.0.0.0
