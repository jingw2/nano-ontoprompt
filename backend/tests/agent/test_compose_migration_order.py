"""Task 2 (M1 stabilization): both Compose files must run a real migration
service before backend/worker services start, and every worker/beat command
must use the current Celery application (app.tasks.celery_app), never the
legacy app.tasks.extraction entry point."""
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
COMPOSE_FILES = [REPO_ROOT / "docker-compose.v2.yml", REPO_ROOT / "docker-compose.agent.yml"]

# the service that fronts the API in each compose file — names differ
# (docker-compose.v2.yml: "backend", docker-compose.agent.yml: "api")
API_SERVICE_BY_FILE = {"docker-compose.v2.yml": "backend", "docker-compose.agent.yml": "api"}


def _compose_services(compose: pathlib.Path) -> dict:
    import yaml
    data = yaml.safe_load(compose.read_text())
    return data.get("services", {})


def test_compose_uses_successful_migration_dependency():
    for compose in COMPOSE_FILES:
        services = _compose_services(compose)
        assert "migration" in services, f"{compose.name} has no migration service"
        api_service = API_SERVICE_BY_FILE[compose.name]
        assert "service_completed_successfully" in str(services[api_service]), (
            f"{compose.name}::{api_service} does not wait for migration completion"
        )
        text = compose.read_text()
        assert "0017_mcp_write_requests" not in text
        assert "app.tasks.celery_app" in text
        assert "-A app.tasks.extraction" not in text
