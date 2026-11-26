import type { RunnerSubscription } from 'fleet/core';

import type { HostRecordFacts } from './host-record.domain';

/** A subscription the host declares: its sampling facts, and its record's revision, state, and settings. */
export interface HostSubscription extends RunnerSubscription, HostRecordFacts {}
