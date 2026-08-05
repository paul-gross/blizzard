"""The OAuth provider seam — :mod:`.provider`, with conformers in ``internal/`` (#92).

An external system behind a seam (``bzh:pluggable-seams``): the Protocol owns the whole
authorize/exchange dance, and all ``httpx`` and provider wire-shape knowledge stays in
``internal/`` (``bzh:dependency-inversion``). The registry (:mod:`.registry`) is keyed
by provider ``name``."""

from __future__ import annotations
