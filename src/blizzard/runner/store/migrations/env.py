"""Alembic environment for the runner store tree.

Driven programmatically by ``blizzard.foundation.store.migrations.MigrationRunner``:
the store URL and the ``script_location`` are set as main options, so there is no
``alembic.ini``. Targets the runner schema metadata for future autogenerate support.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from blizzard.runner.store.schema import metadata as target_metadata

config = context.config


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
