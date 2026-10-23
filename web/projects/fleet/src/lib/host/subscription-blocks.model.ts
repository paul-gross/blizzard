import { SampleMissReason } from '../api/hub';
import type { SubscriptionPace } from './subscription-pace.model';

/** One rate-limit window of a subscription: the figures `KitPaceBar` draws as blocks. */
export interface PaceLine {
  readonly window: string;
  readonly utilizationPct: number;
  readonly elapsedPct: number;
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
    windows: paced ? pace.paceBars.map(({ window, utilizationPct, elapsedPct }) => ({ window, utilizationPct, elapsedPct })) : [],
    stale: pace.freshness === 'stale',
    age: pace.refreshedTag ?? null,
  };
}
