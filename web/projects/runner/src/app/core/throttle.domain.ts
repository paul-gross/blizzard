/** One throttle lever's reading, as the runner reports it. */
export interface ThrottleLever {
  readonly name: string;
  readonly setting: string;
  readonly reading: string;
  readonly until: string | null;
  readonly admits: boolean;
}

/** What holds a runner's floor closed until its subscription resets. */
export interface FloorHold {
  readonly kind: string;
  readonly harness_id: string | null;
  readonly lease_id: string | null;
  readonly window: string | null;
  readonly expected_reset: string;
  readonly awaits_sample: boolean;
  readonly grace_opens_at: string | null;
}

/** An operator's clear of a floor hold. */
export interface FloorClear {
  readonly by: string;
  readonly cleared_at: string;
}

/** A runner's floor: whether it admits work, who owns it, and what holds or cleared it. */
export interface Floor {
  readonly admits: boolean;
  readonly subscription: string | null;
  readonly hold: FloorHold | null;
  readonly clear: FloorClear | null;
}

/** A runner's throttle: its state, the levers declining work, and its floor. `declining` is the runner's own
 * judgment of which levers hold it back, carried as reported. */
export interface Throttle {
  readonly state: string;
  readonly declining: readonly string[];
  readonly floor: Floor;
  readonly levers: readonly ThrottleLever[];
  readonly on_decline: string;
  readonly on_usage_limit: string;
}
