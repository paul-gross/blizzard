import type { RunnerSubscription } from 'fleet';

import type { HostRecordFacts } from './host-record.domain';

/** A subscription the host declares: its sampling facts, and its record's revision, state, and settings. */
export interface HostSubscription extends RunnerSubscription, HostRecordFacts {}
