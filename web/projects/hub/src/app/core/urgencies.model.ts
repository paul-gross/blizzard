import type { UrgencyReference, Urgency } from 'fleet/core';

export function resolveUrgency(ref: UrgencyReference | null | undefined, urgencies: readonly Urgency[] | undefined): Urgency | null {
  return ref == null ? null : urgencies?.find((urgency) => urgency.id === ref.id) ?? null;
}

export function urgencyPill(chunk: { urgency: UrgencyReference | null; terminal: boolean }, urgencies: readonly Urgency[] | undefined): Urgency | null {
  const urgency = resolveUrgency(chunk.urgency, urgencies);
  return chunk.terminal || urgency?.default !== false ? null : urgency;
}

/** The distinct image marks the chunks' banners show: each banner urgency without an emoji but with an icon. */
export function bannerUrgencyIcons(chunks: readonly { urgency?: UrgencyReference | null; terminal: boolean }[], urgencies: readonly Urgency[] | undefined): { key: string; digest: string }[] {
  const icons = new Map<string, string>();
  for (const chunk of chunks) {
    const urgency = urgencyPill({ urgency: chunk.urgency ?? null, terminal: chunk.terminal }, urgencies);
    if (urgency && !urgency.emoji && urgency.icon) icons.set(urgency.id, urgency.icon.digest);
  }
  return [...icons].map(([key, digest]) => ({ key, digest }));
}
