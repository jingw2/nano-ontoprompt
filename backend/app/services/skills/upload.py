"""Parse a user-uploaded Skill file into a manifest (P7C).

Mirrors Claude Desktop/Claude Code's own skill packaging convention: a
`SKILL.md` with YAML frontmatter (`name`, `description`) followed by a
markdown body used as `instructions`, either standalone or bundled in a
zip. A skill manifest never carries executable code (see
app/services/skills/__init__.py), so a zip's non-SKILL.md entries are only
ever text/metadata — anything else, or a path-traversal entry name, is
rejected before any content is read."""
from __future__ import annotations

import io
import re
import zipfile

import yaml

MAX_UPLOAD_BYTES = 2_000_000
MAX_ZIP_ENTRIES = 50
MAX_ZIP_ENTRY_BYTES = 500_000
MAX_ZIP_TOTAL_BYTES = 5_000_000
ALLOWED_ZIP_EXTENSIONS = (".md", ".txt", ".json")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?(.*)\Z", re.DOTALL)


class SkillUploadError(Exception):
    """Rejected skill upload — malformed input or failed a hygiene check."""


def parse_markdown_skill(content: bytes) -> dict:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillUploadError("SKILL_MD_NOT_UTF8") from exc
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillUploadError("SKILL_MD_FRONTMATTER_MISSING")
    front_raw, body = match.group(1), match.group(2)
    try:
        front = yaml.safe_load(front_raw) or {}
    except yaml.YAMLError as exc:
        raise SkillUploadError(f"SKILL_MD_FRONTMATTER_INVALID_YAML:{exc}") from exc
    if not isinstance(front, dict):
        raise SkillUploadError("SKILL_MD_FRONTMATTER_INVALID_YAML")
    manifest: dict = {"name": front.get("name"), "description": front.get("description")}
    if body.strip():
        manifest["instructions"] = body.strip()
    return manifest


def _validate_zip_entry_name(name: str) -> None:
    if name.startswith("/") or ".." in name.split("/"):
        raise SkillUploadError(f"ZIP_ENTRY_PATH_UNSAFE:{name}")


def parse_zip_skill(content: bytes) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise SkillUploadError("ZIP_INVALID") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_ENTRIES:
        raise SkillUploadError("ZIP_TOO_MANY_ENTRIES")
    total_uncompressed = 0
    skill_md_name: str | None = None
    for info in infos:
        _validate_zip_entry_name(info.filename)
        ext = "." + info.filename.rsplit(".", 1)[-1].lower() if "." in info.filename else ""
        if ext not in ALLOWED_ZIP_EXTENSIONS:
            raise SkillUploadError(f"ZIP_ENTRY_EXTENSION_DISALLOWED:{info.filename}")
        if info.file_size > MAX_ZIP_ENTRY_BYTES:
            raise SkillUploadError(f"ZIP_ENTRY_TOO_LARGE:{info.filename}")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_ZIP_TOTAL_BYTES:
            raise SkillUploadError("ZIP_TOTAL_SIZE_TOO_LARGE")
        base = info.filename.rsplit("/", 1)[-1]
        depth = info.filename.count("/")
        if base.lower() == "skill.md" and depth <= 1:
            skill_md_name = info.filename
    if skill_md_name is None:
        raise SkillUploadError("ZIP_SKILL_MD_MISSING")
    return parse_markdown_skill(zf.read(skill_md_name))


def build_manifest_from_upload(filename: str, content: bytes) -> dict:
    if len(content) > MAX_UPLOAD_BYTES:
        raise SkillUploadError("UPLOAD_TOO_LARGE")
    lower = filename.lower()
    if lower.endswith(".md"):
        return parse_markdown_skill(content)
    if lower.endswith(".zip"):
        return parse_zip_skill(content)
    raise SkillUploadError("UPLOAD_EXTENSION_UNSUPPORTED")
