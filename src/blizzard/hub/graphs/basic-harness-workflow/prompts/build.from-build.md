# Build — re-entry after a failed build attempt

This re-entry to the `build` node follows an attempt recorded `fail` for not yet meeting the work item's intent. The
failed attempt's own output rides along: address every point it raises, and keep going in this turn until the intent is
met — or `blizzard runner ask` about what blocks it, rather than ending on the same gap again.
