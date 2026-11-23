"""The display name of each user a response names by id, resolved in one bulk read."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.hub.auth.users import IReadUserRepository


def user_names(users: IReadUserRepository, user_ids: Iterable[str]) -> dict[str, str]:
    """The display name — else the username — of each id that is a user, in one read (``bzh:bulk-reconstitution``);
    an id that is no user (a config apply door, a deleted user) is absent."""
    found = users.get_many(list(set(user_ids)))
    return {user_id: user.display_name or user.username for user_id, user in found.items()}
