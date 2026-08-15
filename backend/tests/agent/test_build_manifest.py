"""E0-IMAGES: signed Agent deployment images (guarded launcher + manifest).

Asserts the guarded-launcher build contract: the stdlib guard/launcher/
bootstrap are copied into the backend image before any dependency install, the
guard runs before pip, every Python service start command in the supported
Compose files routes through the launcher AND verifies the signed build
manifest, generation/sign/verify are deterministic, and the manifest pins the
exact Alembic head (0007_widen_audit_correlation_id).
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
SCRIPTS = BACKEND_DIR / "scripts"
COMPOSE_FILES = [REPO_ROOT / "docker-compose.yml", REPO_ROOT / "docker-compose.v2.yml",
                 REPO_ROOT / "docker-compose.agent.yml"]
# the ops Alembic head the signed manifest must pin
OPS_ALEMBIC_HEAD = "0008_agent_tool_selection"


def test_e0_images_red_contract():
    failures = []
    for path, symbol in (
        ("scripts/generate_build_manifest.py", "def generate_manifest"),
        ("scripts/sign_build_manifest.py", "def sign_manifest"),
        ("scripts/verify_build_manifest.py", "def verify_manifest"),
    ):
        p = SCRIPTS / path.split("/", 1)[1]
        if not p.exists():
            failures.append(f"missing {path}")
        elif symbol not in p.read_text():
            failures.append(f"{path} missing {symbol}")
    backend_df = BACKEND_DIR / "Dockerfile"
    if not backend_df.exists():
        failures.append("missing backend/Dockerfile")
    else:
        source = backend_df.read_text()
        for marker in ("check_python_version.py", "guarded_entrypoint.py", "bootstrap_backend.py"):
            if marker not in source:
                failures.append(f"backend/Dockerfile must copy {marker} before dependency install")
    if not (REPO_ROOT / "docker-compose.agent.yml").exists():
        failures.append("missing docker-compose.agent.yml")
    for compose in COMPOSE_FILES:
        if compose.exists():
            text = compose.read_text()
            if "guarded_entrypoint" not in text:
                failures.append(f"{compose.name} does not route services through the guarded launcher")
            if "verify_build_manifest" not in text:
                failures.append(f"{compose.name} does not verify the build manifest")
    if failures:
        pytest.fail("RED_E0_IMAGES: " + "; ".join(failures))


def _run(script: str, *args, env_extra=None, cwd=BACKEND_DIR):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )


def _compose_services(compose: pathlib.Path) -> dict:
    import yaml
    data = yaml.safe_load(compose.read_text())
    return data.get("services", {})


def test_generate_is_deterministic(tmp_path):
    out1 = tmp_path / "m1.json"
    out2 = tmp_path / "m2.json"
    for out in (out1, out2):
        r = _run("generate_build_manifest.py", "--root", str(REPO_ROOT), "--output", str(out))
        assert r.returncode == 0, r.stderr
        assert out.exists()
    assert out1.read_bytes() == out2.read_bytes(), "manifest generation is not deterministic"


def test_sign_verify_roundtrip_and_tamper_detection(tmp_path):
    manifest = tmp_path / "m.json"
    signed = tmp_path / "m.signed.json"
    r = _run("generate_build_manifest.py", "--root", str(REPO_ROOT), "--output", str(manifest))
    assert r.returncode == 0, r.stderr
    r = _run("sign_build_manifest.py", "--input", str(manifest), "--output", str(signed))
    assert r.returncode == 0, r.stderr
    data = json.loads(signed.read_text())
    assert data.get("signature") and len(data["signature"]) == 64
    r = _run("verify_build_manifest.py", "--manifest", str(signed),
             "--expect-head", OPS_ALEMBIC_HEAD)
    assert r.returncode == 0, r.stderr
    # tamper: flip an image digest -> signature mismatch
    tampered = tmp_path / "tampered.json"
    mutated = dict(data)
    mutated["images"] = dict(data["images"])
    entry = dict(data["images"]["api"])
    entry["digest"] = "0" * 64
    mutated["images"]["api"] = entry
    tampered.write_text(json.dumps(mutated, sort_keys=True))
    r = _run("verify_build_manifest.py", "--manifest", str(tampered),
             "--expect-head", OPS_ALEMBIC_HEAD)
    assert r.returncode != 0 and "SIGNATURE" in (r.stderr + r.stdout).upper()
    # tamper: wrong expected head
    r = _run("verify_build_manifest.py", "--manifest", str(signed),
             "--expect-head", "9999_wrong")
    assert r.returncode != 0


def test_manifest_pins_exact_alembic_head(tmp_path):
    manifest = tmp_path / "m.json"
    r = _run("generate_build_manifest.py", "--root", str(REPO_ROOT), "--output", str(manifest))
    assert r.returncode == 0, r.stderr
    data = json.loads(manifest.read_text())
    assert data["alembic_head"] == OPS_ALEMBIC_HEAD
    for role in ("api", "dispatcher", "artifact_worker", "beat", "watchdog", "sweeper", "frontend"):
        assert role in data["images"], f"missing image role {role}"


def test_compose_expect_head_pins_match_alembic_head():
    """I-9: every compose `--expect-head` must equal the actual Alembic head.

    The compose services verify the signed build manifest before starting and
    fail closed with BUILD_MANIFEST_HEAD_MISMATCH when the pinned head drifts
    from the migrations — so the pins are asserted against
    ScriptDirectory.get_heads() (the real head), not a hand-maintained
    constant, so they can never drift again."""
    from alembic.script import ScriptDirectory

    heads = set(ScriptDirectory(str(BACKEND_DIR / "alembic")).get_heads())
    assert len(heads) == 1, f"expected a single Alembic head, got {sorted(heads)}"
    actual = heads.pop()
    for compose in COMPOSE_FILES:
        assert compose.exists(), f"missing {compose.name}"
        pins = re.findall(r"--expect-head\s+(\S+)", compose.read_text())
        assert pins, f"{compose.name} has no --expect-head pins"
        for pin in pins:
            assert pin == actual, (
                f"{compose.name} pins --expect-head {pin!r} but the Alembic head is "
                f"{actual!r} — docker compose up would fail closed with "
                "BUILD_MANIFEST_HEAD_MISMATCH"
            )


def test_compose_services_route_through_launcher_and_verify():
    for compose in COMPOSE_FILES:
        assert compose.exists(), f"missing {compose.name}"
        services = _compose_services(compose)
        python_services = [
            name for name, spec in services.items()
            if "uvicorn" in str(spec.get("command", "")) or "celery" in str(spec.get("command", ""))
        ]
        assert python_services, f"{compose.name} has no Python service commands"
        for name in python_services:
            command = str(services[name].get("command", ""))
            assert "guarded_entrypoint" in command, f"{compose.name}::{name} not routed through the launcher"
            assert "verify_build_manifest" in command, f"{compose.name}::{name} does not verify the manifest"


def test_backend_dockerfile_guards_before_dependency_install():
    source = (BACKEND_DIR / "Dockerfile").read_text()
    pip_index = source.find("pip install")
    guard_index = source.find("guarded_entrypoint")
    bootstrap_index = source.find("bootstrap_backend")
    check_index = source.find("check_python_version")
    assert -1 not in (pip_index, guard_index, bootstrap_index, check_index)
    # the guard/launcher/bootstrap are copied BEFORE the dependency install
    assert guard_index < pip_index and bootstrap_index < pip_index
    # the entrypoint is the guarded launcher so every role refuses 3.10 first
    assert 'ENTRYPOINT ["python", "scripts/guarded_entrypoint.py"]' in source


def test_agent_compose_image_refs_match_manifest(tmp_path):
    compose = REPO_ROOT / "docker-compose.agent.yml"
    assert compose.exists()
    services = _compose_services(compose)
    manifest = tmp_path / "m.json"
    r = _run("generate_build_manifest.py", "--root", str(REPO_ROOT), "--output", str(manifest))
    assert r.returncode == 0, r.stderr
    data = json.loads(manifest.read_text())
    refs = {v["ref"] for v in data["images"].values()}
    for name, spec in services.items():
        if name in ("db", "redis"):
            continue
        image = str(spec.get("image", ""))
        assert any(image.startswith(ref) for ref in refs), (
            f"docker-compose.agent.yml::{name} image {image!r} not pinned by the manifest ({sorted(refs)})"
        )


def test_agent_compose_is_valid_docker_compose():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable")
    proc = subprocess.run(
        ["docker", "compose", "-f", str(REPO_ROOT / "docker-compose.agent.yml"), "config", "--quiet"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
