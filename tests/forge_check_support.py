"""Raw seeds of the address columns the forge pre-flight check reads, for the component and service tiers."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store.schema import repositories, secrets, work_sources
from tests.support import stamp

_AT = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
_SECRET = "forge-token"


def seed_addresses(
    db_url: str,
    *,
    repository_rows: tuple[tuple[str, str, str, str], ...] = (),
    work_source_rows: tuple[tuple[str, str | None, str | None], ...] = (),
    tenant: str | None = None,
    sealed_secret: bool = True,
) -> None:
    """Insert repositories ``(slug, forge_api_url, owner, repo)`` and GitHub work sources ``(slug, api_base,
    web_base)`` as typed, into ``tenant`` or the store's only one. ``sealed_secret`` false seeds no secret row, for a
    store a real hub starts on."""
    engine = create_engine_from_url(db_url)
    scope = {} if tenant is None else {"tenant_id": tenant}
    try:
        with engine.begin() as conn:
            if repository_rows and sealed_secret:
                conn.execute(
                    sa.insert(secrets).values(
                        stamp(
                            secrets,
                            name=_SECRET,
                            ciphertext="x",
                            nonce="x",
                            key_id="k1",
                            revision=1,
                            replaced_at=_AT,
                            replaced_by="test",
                            created_at=_AT,
                            **scope,
                        )
                    )
                )
            for slug, url, owner, repo in repository_rows:
                conn.execute(
                    sa.insert(repositories).values(
                        stamp(
                            repositories,
                            repository_id=f"repo_{slug}",
                            slug=slug,
                            name=slug,
                            forge_api_url=url,
                            owner=owner,
                            repo=repo,
                            base_branch="master",
                            secret_name=_SECRET,
                            revision=1,
                            created_at=_AT,
                            created_by="test",
                            **scope,
                        )
                    )
                )
            for slug, api_base, web_base in work_source_rows:
                conn.execute(
                    sa.insert(work_sources).values(
                        stamp(
                            work_sources,
                            work_source_id=f"wsrc_{slug}",
                            slug=slug,
                            name=slug,
                            provider="github",
                            locator=f"acme/{slug}",
                            api_base=api_base,
                            web_base=web_base,
                            annotate=False,
                            secret_name=None,
                            revision=1,
                            created_at=_AT,
                            created_by="test",
                            **scope,
                        )
                    )
                )
    finally:
        engine.dispose()
