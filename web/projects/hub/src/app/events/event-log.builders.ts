// The event-log domain builders specs build their inputs from — domain literals only, no wire, no Angular.
// The wire builder (`eventView` in `fleet/testing`) stays for the translator and query specs and stubbed answers.
import { fixtureInstant } from 'fleet/testing';

import type { EventLogEntry } from './event-log.domain';

/** A stored `lease-minted` info event from runner `rn_01`, a minute ago, naming no chunk. */
export function eventLogEntry(overrides: Partial<EventLogEntry> = {}): EventLogEntry {
  return {
    id: 1,
    recordedAt: fixtureInstant(-60),
    severity: 'info',
    kind: 'lease-minted',
    message: 'lease minted',
    runnerId: 'rn_01',
    runnerName: null,
    chunkId: null,
    leaseId: null,
    nodeName: null,
    detail: null,
    ...overrides,
  };
}
