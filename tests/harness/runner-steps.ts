import { expect } from '@playwright/test';
import type { Verbs } from './client/engine';
import type { MockChunkSpec } from './client/mock';
import type { ChunkDetail, ChunkFlowPlaceView, ChunkStatus, FlowPlaceState } from './client/wire/hub/types.gen';
import type { MockHub } from './mock-world';
import type { RunnerHost } from './runner-host';
import type { WorkerCheckpoint } from './worker-checkpoint';

// Waits on a stepped runner host: every wait is a pass of the host, never a clock.

const STALL_PASSES = 40;
const RUNNER = 'runner-local';
/** The checkpoint a `holdingChunk` worker's spawn turn waits at. */
export const SPAWN_HOLD = 'spawn';

/** How long a `stepUntil` wait may go without progress, and which part of its read is progress. */
export interface StepWait<T> {
  /** Consecutive passes that may observe no progress before the wait fails; default `STALL_PASSES`. */
  stallPasses?: number;
  /**
   * The part of the read state whose change is progress; default the whole state. A read carrying a field that moves
   * on a pass with no progress — a poll time, a heartbeat, a timestamp — projects it out here, or the wait never stalls
   * and only the test's timeout ends it.
   */
  progress?: (state: T) => unknown;
}

/**
 * Steps the host's sole runner until `reached` holds, failing only after `stallPasses` consecutive passes in which the
 * `progress` of the read state did not change, with the last state it saw. A chunk advances at its workers' own speed,
 * so the passes it needs grow with how loaded the machine is; a read that shows each step of the way resets the count,
 * while one that changes only once `reached` holds, such as a bare status, is no better than a total count.
 */
export async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean, wait: StepWait<NoInfer<T>> = {}): Promise<T> {
  const { stallPasses = STALL_PASSES, progress = (state: T) => state } = wait;
  let state = await read();
  let seen = JSON.stringify(progress(state));
  for (let stalled = 0; !reached(state); ) {
    if (stalled === stallPasses) throw new Error(`no progress within ${stallPasses} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    state = await read();
    const now = JSON.stringify(progress(state));
    stalled = now === seen ? stalled + 1 : 0;
    seen = now;
  }
  return state;
}

/** A chunk-detail field name, checked against the hub's wire so a rename there fails the typecheck here. */
type ChunkField<K extends keyof ChunkDetail> = K;

/** The fields of a chunk detail `chunkProgress` reads; a loosely typed body, or a narrower read, satisfies it. */
type ChunkProgressSource = { status: string } & Partial<Pick<ChunkDetail, ChunkField<'current_node_name' | 'latest_epoch' | 'pending'>>> & {
  [K in ChunkField<'history' | 'bounces' | 'open_prs' | 'closed_prs' | 'landed_repos'>]?: readonly unknown[];
} & { [K in ChunkField<'flow_places'>]?: readonly Pick<ChunkFlowPlaceView, 'flow_id' | 'state'>[] };

/** The flow-place states a waiting submission cycles through on every poll, which `chunkProgress` reads as one. */
const IN_LINE: ReadonlySet<FlowPlaceState> = new Set(['landing', 'next', 'waiting', 'polling']);

/**
 * A chunk detail's progress, for `StepWait.progress`: its status, node, epoch, transitions, pending hub node, bounces,
 * pull requests, landed repos, and each flow it holds a place on, read as in line, awaiting a merge, or passed over —
 * never a poll time, a place's position, the in-line cycle each poll turns, or the flow record, which a poll grows.
 */
export function chunkProgress(detail: ChunkProgressSource) {
  return {
    status: detail.status,
    node: detail.current_node_name,
    epoch: detail.latest_epoch,
    transitions: detail.history?.length,
    pending: detail.pending && { kind: detail.pending.kind, node: detail.pending.node_name },
    bounces: detail.bounces?.length,
    prs: [detail.open_prs?.length, detail.closed_prs?.length],
    landed: detail.landed_repos?.length,
    places: detail.flow_places?.map((place) => [place.flow_id, IN_LINE.has(place.state) ? 'in-line' : place.state]),
  };
}

/** Steps until the hub chunk's status meets `reached`, waiting on its `chunkProgress` rather than on the status alone. */
export async function stepChunkUntil(host: RunnerHost, api: Pick<Verbs, 'get'>, chunkId: string, reached: (status: ChunkStatus) => boolean, wait: Omit<StepWait<ChunkProgressSource>, 'progress'> = {}): Promise<ChunkStatus> {
  const read = async () => (await api.get<ChunkDetail>(`/api/chunks/${chunkId}`)).body;
  return (await stepUntil(host, read, (detail) => reached(detail.status), { ...wait, progress: chunkProgress })).status;
}

/** The fields of a lease `leaseProgress` reads. */
interface LeaseProgressSource {
  lease_id: string;
  node_name: string;
  state: string;
  closed_at: string | null;
}

/** A lease's progress, or a list of leases', for `StepWait.progress`: node, state, and closure — never a heartbeat. */
export function leaseProgress(leases: LeaseProgressSource | readonly LeaseProgressSource[] | undefined) {
  return [leases ?? []].flat().map((lease) => [lease.lease_id, lease.node_name, lease.state, lease.closed_at !== null]);
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
