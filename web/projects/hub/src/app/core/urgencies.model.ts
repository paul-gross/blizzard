import type { UrgencyRef, UrgencyView } from 'fleet';

export function resolveUrgency(ref: UrgencyRef | null | undefined, urgencies: readonly UrgencyView[] | undefined): UrgencyView | null {
  return ref == null ? null : urgencies?.find((urgency) => urgency.urgency_id === ref.urgency_id) ?? null;
}

export function urgencyPill(chunk: { urgency: UrgencyRef | null; terminal: boolean }, urgencies: readonly UrgencyView[] | undefined): UrgencyView | null {
  const urgency = resolveUrgency(chunk.urgency, urgencies);
  return chunk.terminal || urgency?.default !== false ? null : urgency;
}

/** The distinct image marks the chunks' banners show: each banner urgency without an emoji but with an icon. */
export function bannerUrgencyIcons(chunks: readonly { urgency?: UrgencyRef | null; terminal: boolean }[], urgencies: readonly UrgencyView[] | undefined): { key: string; digest: string }[] {
  const icons = new Map<string, string>();
  for (const chunk of chunks) {
    const urgency = urgencyPill({ urgency: chunk.urgency ?? null, terminal: chunk.terminal }, urgencies);
    if (urgency && !urgency.emoji && urgency.icon) icons.set(urgency.urgency_id, urgency.icon.digest);
  }
  return [...icons].map(([key, digest]) => ({ key, digest }));
}
