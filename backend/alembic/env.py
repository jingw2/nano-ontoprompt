from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_python_version import require_supported_python  # noqa: E402

require_supported_python()

import os
import re
from logging.config import fileConfig
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from the environment if DATABASE_URL is set.
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def _search_path_schema(url: str | None) -> str | None:
    """When DATABASE_URL carries a `-csearch_path=schema[,...]` libpq option
    (schema-isolated test fixtures use this), pin Alembic's own version
    table to that schema explicitly. Without this, Alembic resolves the
    unqualified `alembic_version` table via search_path and can silently
    find an existing tracking table in a later schema (e.g. `public`) when
    the isolated schema doesn't have one yet — it then believes migrations
    are already at head and skips creating every table in the isolated
    schema, while the caller's own inserts fall through to the real tables.
    Returns None (no override — current behavior) when DATABASE_URL carries
    no search_path option, which is always true in production/dev."""
    if not url:
        return None
    options = parse_qs(urlparse(url).query).get("options", [None])[0]
    if not options:
        return None
    match = re.search(r"-csearch_path=([^,\s]+)", unquote(options))
    return match.group(1) if match else None


version_table_schema = _search_path_schema(database_url)

# add your model's MetaData object here
# for 'autogenerate' support
# Import Base and all models so that autogenerate can detect them.
from app.database import Base  # noqa: E402
from app.models import load_all_models  # noqa: E402

target_metadata = load_all_models()

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=version_table_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            version_table_schema=version_table_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
