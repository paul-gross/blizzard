/**
 * The node-step key codec — the single owner of the `(nodeId, epoch)` ↔ string mapping
 * used to identify one node-step across the chunk detail page: the Node history tab's
 * `step` URL param, `ChunkTimeline`'s row selection, and the transcript step grouping
 * (`transcript-steps.ts`) all key the same pair the same way, so they stay joinable.
 * `${nodeId}:${epoch}` is `transcript-steps.ts`'s own pre-existing format, adopted here
 * rather than invented (`canon:one-owner`).
 */
export function nodeStepKey(nodeId: string, epoch: number): string {
  return `${nodeId}:${epoch}`;
}

/** The inverse of {@link nodeStepKey} — `null` for a string that isn't one, e.g. a
 * `step` URL param naming nothing (that dead-link state is the reading tab's own to
 * resolve, not this codec's — it only refuses to hand back a nonsensical pair). */
export function parseNodeStepKey(key: string): { nodeId: string; epoch: number } | null {
  const sep = key.lastIndexOf(':');
  if (sep <= 0) return null;
  const nodeId = key.slice(0, sep);
  const epochStr = key.slice(sep + 1);
  if (!/^\d+$/.test(epochStr)) return null;
  const epoch = Number(epochStr);
  if (!Number.isInteger(epoch)) return null;
  return { nodeId, epoch };
}
