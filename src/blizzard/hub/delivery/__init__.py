"""The delivery domain — the generic hub command node executor.

Delivery is authored as graph CONTENT, not an engine special case: a hub node names one of the hub's
actions, and the executor takes a pass of it in-process and routes the choice it answers.
"""

from __future__ import annotations
