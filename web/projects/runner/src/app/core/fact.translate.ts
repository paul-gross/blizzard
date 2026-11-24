import type { runnerApi } from 'fleet/runner-shell';

import type { Fact } from './fact.domain';

export function fromFactView(wire: runnerApi.FactView): Fact {
  return {
    seq: wire.seq,
    kind: wire.kind,
    chunk_id: wire.chunk_id ?? null,
    project_id: wire.project_id ?? null,
    lease_id: wire.lease_id ?? null,
    created_at: wire.created_at,
    acked_at: wire.acked_at ?? null,
  };
}
