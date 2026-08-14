# E0-IMAGES: signed Agent deployment image (guarded launcher + baked manifest).
#
# The stdlib guard/launcher/bootstrap are copied BEFORE any dependency install
# and the guard runs before pip; the signed build manifest is baked into
# /manifests/agent.json at build time; every role (api/dispatcher/worker/beat/
# watchdog/sweeper) starts through the guarded launcher and verifies the
# manifest before exec.  No pip, migration, or Python service command precedes
# the guard.
FROM python:3.12-slim
WORKDIR /app

# stdlib-only guard + launcher + bootstrap + E0-DB migration wrapper, before pip
COPY scripts/check_python_version.py scripts/guarded_entrypoint.py scripts/bootstrap_backend.py ./scripts/
COPY scripts/run_migrations.py scripts/verify_schema_revision.py ./scripts/
COPY scripts/generate_build_manifest.py scripts/sign_build_manifest.py scripts/verify_build_manifest.py ./scripts/
COPY requirements.txt .
# the guard runs before pip install (bootstrap_backend.py enforces the floor)
RUN python scripts/bootstrap_backend.py

COPY . .

# bake the signed build manifest; the frontend lock digest comes from the
# build system (the backend image carries no frontend sources)
ARG FRONTEND_MANIFEST_DIGEST=0000000000000000000000000000000000000000000000000000000000000000
RUN mkdir -p /manifests \
 && python scripts/generate_build_manifest.py --backend-dir /app \
      --frontend-manifest-digest "$FRONTEND_MANIFEST_DIGEST" \
      --output /manifests/agent.json \
 && python scripts/sign_build_manifest.py --input /manifests/agent.json --output /manifests/agent.json

# every role refuses unsupported Python before any service import
ENTRYPOINT ["python", "scripts/guarded_entrypoint.py"]
