FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The collector repo is bind-mounted from the host, so its .git is owned by a
# different uid than the one running in the container.
RUN git config --global --add safe.directory '*'

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY indexer/ ./indexer/
COPY web/ ./web/
COPY mcp_server/ ./mcp_server/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV COLLECTOR_REPO=/data/collector \
    OUTPUT_DIR=/app/dist \
    CACHE_DIR=/app/.cache \
    PORT=8080

EXPOSE 8080 8081
ENTRYPOINT ["./entrypoint.sh"]
