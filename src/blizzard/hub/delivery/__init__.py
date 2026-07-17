"""The delivery domain — the deliver node's forge-landing seam.

Delivery executes at the hub, through the singleton coordinator's strict-FIFO merge
queue. This package owns the delivery seam (:mod:`.forge`) — the
Protocol the coordinator lands branch artifacts through — and its reference binding
under ``internal/`` (``bzh:pluggable-seams``): the mock forge in tests, GitHub in
production.
"""

from __future__ import annotations
