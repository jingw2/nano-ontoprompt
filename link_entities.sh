#!/usr/bin/env bash
# 在 Docker backend 容器内运行 link_entities.py（无需本地安装 sqlalchemy）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONTAINER="${ONTOPROMPT_BACKEND_CONTAINER:-nano-ontoprompt-backend-1}"
CSV="${ONTOPROMPT_SNOMED_CSV:-$ROOT/snomed_mental_health.csv}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "✗ 容器未运行: $CONTAINER" >&2
  echo "  请先: docker compose up -d" >&2
  exit 1
fi

docker cp "$ROOT/link_entities.py" "$CONTAINER:/tmp/link_entities.py"
docker cp "$CSV" "$CONTAINER:/tmp/snomed_mental_health.csv"

exec docker exec "$CONTAINER" python /tmp/link_entities.py \
  --csv /tmp/snomed_mental_health.csv \
  "$@"
