import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import type { HostRecordFacts, HostRecordState } from './host-record.domain';

const stateOf = openLookup<runnerApi.HostWorkspaceView['state'], HostRecordState>(
  { active: 'active', retired: 'retired' },
  (raw) => ({ kind: 'unknown', raw }),
);

/** A host record's revision, state, and stated settings, as a workspace or subscription read carries them. */
export function hostRecordFacts(wire: Pick<runnerApi.HostWorkspaceView, 'revision' | 'state' | 'settings'>): HostRecordFacts {
  return { revision: wire.revision ?? null, state: stateOf(wire.state ?? 'active'), settings: { ...(wire.settings ?? {}) } };
}
