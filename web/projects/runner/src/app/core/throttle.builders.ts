import type { Floor, Throttle, ThrottleLever } from './throttle.domain';

/** An open floor this runner owns, nothing holding it. */
export function floor(overrides: Partial<Floor> = {}): Floor {
  return { admits: true, subscription: null, hold: null, clear: null, ...overrides };
}

/** One lever admitting work. */
export function throttleLever(overrides: Partial<ThrottleLever> = {}): ThrottleLever {
  return { name: 'pace', setting: '', reading: '', until: null, admits: true, ...overrides };
}

/** A throttle admitting work, no lever declining. */
export function throttle(overrides: Partial<Throttle> = {}): Throttle {
  return {
    state: 'admitting',
    declining: [],
    floor: floor(),
    levers: [],
    on_decline: 'wait',
    on_usage_limit: 'wait',
    ...overrides,
  };
}
