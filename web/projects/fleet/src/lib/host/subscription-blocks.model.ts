import { SampleMissReason } from '../api/hub';
import type { SubscriptionPace } from './subscription-pace.model';

/** How many blocks a subscription window's pace line draws — one per tenth of the window. */
export const PACE_CELLS = 10;

/** One block of a pace line: filled to the window's usage, and marked where its clock stands. */
export interface PaceCell {
  readonly on: boolean;
  /** The clock sits on this block's left edge — or, on the last block, `clockAfter` its right. */
  readonly clockBefore: boolean;
  readonly clockAfter: boolean;
}

/** One rate-limit window of a subscription, as blocks: usage filled, the window's clock a notch. */
export interface PaceLine {
  readonly window: string;
  readonly cells: readonly PaceCell[];
  /** Usage ahead of the clock — on pace to exhaust the window before it resets. */
  readonly ahead: boolean;
  /** The figures, for the tooltip: "5h · 62% used · 70% of window". */
  readonly hint: string;
}

/** One subscription as a line of blocks: its windows' pace lines, or why it has none. */
export interface SubscriptionLine {
  readonly slug: string;
  readonly name: string;
  /** `paced` draws `windows`; `lapsed` is a credential to log in again; `unsampled` has no window to draw. */
  readonly state: 'paced' | 'lapsed' | 'unsampled';
  readonly windows: readonly PaceLine[];
  /** Its last good sample is past the stale age — the line dims. */
  readonly stale: boolean;
  /** Its last good sample's age, bracketed — "[50s]"; `null` when never sampled. */
  readonly age: string | null;
  /** What an `unsampled` line says in place of its blocks — its newest reading's condition when
   * that reading carried no windows; absent draws a dash for no reading at all. */
  readonly note?: string | null;
}

function toTenths(pct: number): number {
  return Math.round(Math.min(100, Math.max(0, pct)) / (100 / PACE_CELLS));
}

/** One window as {@link PACE_CELLS} blocks, its usage filled and its clock notched. */
export function paceLine(window: string, utilizationPct: number, elapsedPct: number): PaceLine {
  const used = toTenths(utilizationPct);
  const clock = toTenths(elapsedPct);
  return {
    window,
    cells: Array.from({ length: PACE_CELLS }, (_unused, i) => ({
      on: i < used,
      clockBefore: i === clock,
      clockAfter: i === PACE_CELLS - 1 && clock === PACE_CELLS,
    })),
    ahead: utilizationPct > elapsedPct,
    hint: `${window} · ${Math.round(utilizationPct)}% used · ${Math.round(elapsedPct)}% of window`,
  };
}

/** Whether a subscription's newest reading is a lapsed credential. */
export const isLapsed = (pace: SubscriptionPace): boolean => pace.condition === SampleMissReason.CREDENTIAL_LAPSED;

/** A subscription's pace rows folded to its line of blocks. */
export function subscriptionLine(pace: SubscriptionPace): SubscriptionLine {
  const lapsed = isLapsed(pace);
  const paced = !lapsed && pace.sampledAt !== null && pace.paceBars.length > 0;
  return {
    slug: pace.slug,
    name: pace.name,
    state: lapsed ? 'lapsed' : paced ? 'paced' : 'unsampled',
    windows: paced ? pace.paceBars.map((bar) => paceLine(bar.window, bar.utilizationPct, bar.elapsedPct)) : [],
    stale: pace.freshness === 'stale',
    age: pace.refreshedTag ?? null,
  };
}
