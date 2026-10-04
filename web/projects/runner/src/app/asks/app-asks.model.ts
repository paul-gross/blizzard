import { ageMs, compactRef, formatHeldFor, type runnerApi } from 'fleet';

import type { AskRow } from './app-asks-view';

/** `42m` since the ask was raised, or `—` when the timestamp is skew-broken (`bzh:utc-instants`). */
export function askedForLabel(askedAt: string, now: number): string {
  const age = ageMs(askedAt, now);
  return age === null ? '—' : formatHeldFor(age);
}

/** One {@link AskRow} per open ask, in wire order, aged against `now`. */
export function askRows(asks: readonly runnerApi.AskView[], now: number): readonly AskRow[] {
  return asks.map((ask) => ({
    questionId: ask.question_id,
    chunkRef: compactRef(ask.chunk_id),
    askedFor: askedForLabel(ask.asked_at, now),
    question: ask.question,
  }));
}
