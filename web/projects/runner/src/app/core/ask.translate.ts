import type { runnerApi } from 'fleet/runner-shell';

import type { Ask } from './ask.domain';

export function fromAskView(wire: runnerApi.AskView): Ask {
  return {
    question_id: wire.question_id,
    chunk_id: wire.chunk_id,
    project_id: wire.project_id ?? null,
    lease_id: wire.lease_id,
    session_id: wire.session_id ?? null,
    harness_id: wire.harness_id ?? null,
    question: wire.question,
    options: [...wire.options],
    asked_at: wire.asked_at,
  };
}
