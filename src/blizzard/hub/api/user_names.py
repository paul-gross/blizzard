"""The display name of each user a response names by id, resolved in one bulk read."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.hub.composition import TenantServices


def user_names(services: TenantServices, user_ids: Iterable[str]) -> dict[str, str]:
    """The display name — else the username — of each id that is a user, in one read (``bzh:bulk-reconstitution``);
    an id that is no user (a config apply door, a deleted user) is absent."""
    users = services.users.get_many(list(set(user_ids)))
    return {user_id: user.display_name or user.username for user_id, user in users.items()}
