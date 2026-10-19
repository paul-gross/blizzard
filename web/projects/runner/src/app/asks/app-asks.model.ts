import { ageMs, compactRef, formatHeldFor } from 'fleet';

import type { PanelAsk } from '../core/panel-reads.model';

import type { AskRow } from './app-asks-view';

/** `42m` since the ask was raised, or `—` when the timestamp is skew-broken (`bzh:utc-instants`). */
export function askedForLabel(askedAt: string, now: number): string {
  const age = ageMs(askedAt, now);
  return age === null ? '—' : formatHeldFor(age);
}

/** One {@link AskRow} per open ask, in wire order, aged against `now`. */
export function askRows(asks: readonly PanelAsk[], now: number): readonly AskRow[] {
  return asks.map((ask) => ({
    questionId: ask.question_id,
    runner: ask.owner.label,
    chunkRef: compactRef(ask.chunk_id),
    askedFor: askedForLabel(ask.asked_at, now),
    question: ask.question,
  }));
}
