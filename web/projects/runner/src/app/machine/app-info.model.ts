import { ageMs, formatAge, type runnerApi } from 'fleet';

/**
 * The runner status section, or `null` when the body is malformed (e.g. `{}` from a
 * misrouted proxy) — the panel must render the degraded state, not throw on
 * `hub.endpoint` mid-template.
 */
export function wellFormedRunner(
  data: runnerApi.RunnerStatusView | null | undefined,
): runnerApi.RunnerStatusView | null {
  return data?.hub && data.capacities && data.pause ? data : null;
}

/** `-34s` since the last successful PULL, or `never` before first contact. */
export function lastFlushLabel(view: runnerApi.RunnerStatusView | null, now: number): string {
  const contactAt = view?.hub.last_contact_at ?? null;
  if (contactAt === null) return 'never';
  const age = ageMs(contactAt, now);
  return age === null ? '—' : formatAge(age);
}

/** `-34s` since the runner loop's last tick, or `—` before the first tick. */
export function lastTickLabel(view: runnerApi.RunnerStatusView | null, now: number): string {
  const tickAt = view?.last_tick_at ?? null;
  if (tickAt === null) return '—';
  const age = ageMs(tickAt, now);
  return age === null ? '—' : formatAge(age);
}
