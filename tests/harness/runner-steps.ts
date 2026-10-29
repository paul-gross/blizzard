import { expect } from '@playwright/test';
import type { MockChunkSpec } from './client/mock';
import type { MockHub } from './mock-world';
import type { RunnerHost } from './runner-host';

// Waits on a stepped runner host: every wait is a pass of the host, never a clock.

const MAX_STEPS = 40;
const RUNNER = 'runner-local';

/** Holds the worker's judgement turn, and so its lease, open: the spawn turn has ended and recorded its session. */
export const HOLD_OPEN = 'import time\ntime.sleep(120)\n';

/** Steps the host's sole runner until `reached` holds, failing after `MAX_STEPS` passes with the last state it saw. */
export async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean): Promise<T> {
  let state = await read();
  for (let pass = 0; !reached(state); pass++) {
    if (pass === MAX_STEPS) throw new Error(`not reached within ${MAX_STEPS} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    state = await read();
  }
  return state;
}

/** Steps until the chunk's sole lease is running and the mock harness has recorded its spawn credential, current as the runner last issued it. */
export async function runningLeaseWithWorker(host: RunnerHost, chunkId: string) {
  const runner = host.runners[RUNNER]!;
  const lease = await stepUntil(
    host,
    async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running'),
    (found) => found !== undefined,
  );
  const headers = await stepUntil(
    host,
    async () => {
      try {
        // The runner re-mints a lease's token for each resume, so the record is read until the credential it holds answers.
        const candidate = host.workerHeaders(lease!.lease_id);
        return (await runner.leaseArtifacts(lease!.lease_id, { scope: 'graph', headers: candidate })).status === 200 ? candidate : undefined;
      } catch {
        return undefined;
      }
    },
    (found) => found !== undefined,
  );
  return { lease: lease!, headers: headers! };
}

/** A one-node chunk whose worker judgement turn is held open, so its lease stays running for the spec to read through. */
export function holdingChunk(): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: 'pass\n',
        judgement_prompt: HOLD_OPEN,
        choices: [{ name: 'pass', description: 'committed and green', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

/** Seeds `spec` and steps until its sole lease is running with a worker credential that answers. */
export async function seededLeaseWithWorker(mockHub: MockHub, host: RunnerHost, spec: MockChunkSpec) {
  const seeded = await mockHub.seed.chunk(spec);
  expect(seeded.status).toBe(201);
  const { lease, headers } = await runningLeaseWithWorker(host, seeded.body.chunk_id);
  return { leaseId: lease.lease_id, headers };
}
