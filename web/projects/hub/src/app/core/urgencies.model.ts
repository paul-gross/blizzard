import type { UrgencyRef, UrgencyView } from 'fleet';

export function resolveUrgency(ref: UrgencyRef | null | undefined, urgencies: readonly UrgencyView[] | undefined): UrgencyView | null {
  return ref == null ? null : urgencies?.find((urgency) => urgency.urgency_id === ref.urgency_id) ?? null;
}

export function urgencyPill(chunk: { urgency: UrgencyRef | null; terminal: boolean }, urgencies: readonly UrgencyView[] | undefined): UrgencyView | null {
  const urgency = resolveUrgency(chunk.urgency, urgencies);
  return chunk.terminal || urgency?.default !== false ? null : urgency;
}
