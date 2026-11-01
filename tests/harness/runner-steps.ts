import { expect } from '@playwright/test';
import type { MockChunkSpec } from './client/mock';
import type { MockHub } from './mock-world';
import type { RunnerHost } from './runner-host';
import type { WorkerCheckpoint } from './worker-checkpoint';

// Waits on a stepped runner host: every wait is a pass of the host, never a clock.

const MAX_STEPS = 40;
const RUNNER = 'runner-local';
/** The checkpoint a `holdingChunk` worker's spawn turn waits at. */
export const SPAWN_HOLD = 'spawn';

/** Steps the host's sole runner until `reached` holds, failing after `maxSteps` passes (default `MAX_STEPS`) with the last state it saw. */
export async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean, maxSteps = MAX_STEPS): Promise<T> {
  let state = await read();
  for (let pass = 0; !reached(state); pass++) {
    if (pass === maxSteps) throw new Error(`not reached within ${maxSteps} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    state = await read();
  }
  return state;
}

/**
 * Steps the host's sole runner until `reached` holds, failing only after `stallPasses` consecutive passes in which the
 * read state did not change, with the last state it saw. A multi-node chunk advances at its workers' own speed, one
 * spawned process per node, so the passes it needs grow with how loaded the machine is; counting passes in total makes
 * the wait race that speed, while counting passes that observed no progress waits on the progress itself.
 */
export async function stepUntilStalled<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean, stallPasses = MAX_STEPS): Promise<T> {
  let state = await read();
  let seen = JSON.stringify(state);
  for (let stalled = 0; !reached(state); ) {
    if (stalled === stallPasses) throw new Error(`no progress within ${stallPasses} passes; last state ${seen}\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    state = await read();
    const now = JSON.stringify(state);
    stalled = now === seen ? stalled + 1 : 0;
    seen = now;
  }
  return state;
}

/**
 * Steps until the chunk's lease is running, then waits for its worker to reach `checkpoint`'s `point`, held in its
 * spawn turn; returns the lease. Passes claim and spawn the worker, but no pass moves it from there to the checkpoint:
 * it gets there in its own process, at its own speed, so its arrival is awaited, never counted in passes. A lease reads
 * `running` only while its spawn process lives, so the hold is what keeps it readable as running.
 */
export async function stepUntilHeld(host: RunnerHost, chunkId: string, checkpoint: WorkerCheckpoint, point: string) {
  const runner = host.runners[RUNNER]!;
  const lease = await stepUntil(
    host,
    async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running'),
    (found) => found !== undefined,
  );
  await checkpoint.reached(point);
  return lease!;
}

/**
 * Steps until the chunk's lease is running and its worker held at `checkpoint`'s `point`, with the worker credential
 * the mock harness recorded for it. The mock records the credential before it runs its prompt, so a worker at the
 * checkpoint has recorded it, and a held spawn takes no resume to re-mint it.
 */
export async function runningLeaseWithWorker(host: RunnerHost, chunkId: string, checkpoint: WorkerCheckpoint, point = SPAWN_HOLD) {
  const lease = await stepUntilHeld(host, chunkId, checkpoint, point);
  return { lease, headers: host.workerHeaders(lease.lease_id) };
}

/**
 * A one-node chunk whose spawn turn waits at `checkpoint`'s `point`, so its lease stays running until the test releases
 * it, or the checkpoint's teardown does.
 */
export function holdingChunk(checkpoint: WorkerCheckpoint, point = SPAWN_HOLD): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: checkpoint.snippet(point),
        judgement_prompt: "verdict('pass', 'committed and green')\n",
        choices: [{ name: 'pass', description: 'committed and green', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

/** Seeds `spec`, whose spawn turn is held at `checkpoint`'s `SPAWN_HOLD`, and steps until its lease is running and its worker held, with its worker credential. */
export async function seededLeaseWithWorker(mockHub: MockHub, host: RunnerHost, checkpoint: WorkerCheckpoint, spec: MockChunkSpec = holdingChunk(checkpoint)) {
  const seeded = await mockHub.seed.chunk(spec);
  expect(seeded.status).toBe(201);
  const { lease, headers } = await runningLeaseWithWorker(host, seeded.body.chunk_id, checkpoint);
  return { leaseId: lease.lease_id, headers };
}
