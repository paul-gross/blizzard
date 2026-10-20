"""Fold a store's migration history up to a floor release into one baseline revision.

``scripts/fold_store.py`` is the entry point. The floor release's own code never runs in this process: its store
is built and rendered by its installation's interpreter
(``src/blizzard/tools/store_fold/render_floor.py``), and this package reads the finished output and writes files."""
