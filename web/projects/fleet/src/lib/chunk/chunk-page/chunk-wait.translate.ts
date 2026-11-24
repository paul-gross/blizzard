import type { BounceView, ChunkFlowPlaceView, FlowFactView, ChunkDeleteRefusal as WireChunkDeleteRefusal, WaitKind, MigrationSource as WireMigrationSource, MigrationMode as WireMigrationMode, UsageKind as WireUsageKind, FlowPlaceState, FlowFactKind as WireFlowFactKind, FlowVerdict as WireFlowVerdict } from '../../api/hub';
import { openLookup } from '../../core/open-lookup';
import type { ChunkDetail, ChunkDeleteRefusal, ChunkWaitKind, MigrationSource, MigrationMode, UsageKind, FlowState, FlowFactKind, FlowVerdict } from './chunk-detail.model';

export const fromChunkWaitKind = openLookup<WaitKind, ChunkWaitKind>({
  pending: { kind: 'pending' },
  'awaiting-merge': { kind: 'awaiting-merge' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromChunkDeleteRefusal = openLookup<WireChunkDeleteRefusal, ChunkDeleteRefusal>({
  status: { kind: 'status' },
  dependents: { kind: 'dependents' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromMigrationSource = openLookup<WireMigrationSource, MigrationSource>({
  'authored-edge': { kind: 'authored-edge' }, intent: { kind: 'intent' },
  'follow-latest': { kind: 'follow-latest' }, restart: { kind: 'restart' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromMigrationMode = openLookup<WireMigrationMode, MigrationMode>({
  auto: { kind: 'auto' }, forced: { kind: 'forced' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromUsageKind = openLookup<WireUsageKind, UsageKind>({
  spawn: { kind: 'spawn' }, resume: { kind: 'resume' }, judge: { kind: 'judge' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromFlowState = openLookup<FlowPlaceState, FlowState>({
  landing: { kind: 'landing' }, next: { kind: 'next' }, waiting: { kind: 'waiting' }, polling: { kind: 'polling' },
  'awaiting-merge': { kind: 'awaiting-merge' }, 'passed-over': { kind: 'passed-over' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromFlowFactKind = openLookup<WireFlowFactKind, FlowFactKind>({
  submitted: { kind: 'submitted' }, 'turn-started': { kind: 'turn-started' }, 'turn-ended': { kind: 'turn-ended' },
  landed: { kind: 'landed' }, withdrawn: { kind: 'withdrawn' },
}, (raw) => ({ kind: 'unknown', raw }));

export const fromFlowVerdict = openLookup<WireFlowVerdict, FlowVerdict>({
  landed: { kind: 'landed' }, bounced: { kind: 'bounced' },
}, (raw) => ({ kind: 'unknown', raw }));

/** One flow place of a chunk, its state kept open. */
export function fromChunkFlowPlaceView(f: ChunkFlowPlaceView): ChunkDetail['flow_places'][number] {
  return { branch: f.branch, flow_id: f.flow_id, in_line: f.in_line, next_poll_at: f.next_poll_at,
    position: f.position, repository_name: f.repository_name, repository_slug: f.repository_slug, resource: f.resource, state: fromFlowState(f.state) };
}

/** One recorded flow fact, its kind and verdict kept open. */
export function fromFlowFactView(f: FlowFactView): ChunkDetail['flow_record'][number] {
  return { at: f.at, chunk_id: f.chunk_id, flow_id: f.flow_id, id: f.id, kind: fromFlowFactKind(f.kind),
    outcome: f.outcome, revision: f.revision, rework: f.rework, submission_id: f.submission_id, verdict: f.verdict === null ? null : fromFlowVerdict(f.verdict), wait: f.wait };
}

/** One bounce of a chunk. */
export function fromBounceView(b: BounceView): ChunkDetail['bounces'][number] {
  return { cause: b.cause, envelope: b.envelope, recorded_at: b.recorded_at, rework: b.rework, withdrawn: b.withdrawn };
}
