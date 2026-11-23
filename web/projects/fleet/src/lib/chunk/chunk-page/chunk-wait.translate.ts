import type { ChunkDeleteRefusal as WireChunkDeleteRefusal, WaitKind, MigrationSource as WireMigrationSource, MigrationMode as WireMigrationMode, UsageKind as WireUsageKind, FlowPlaceState, FlowFactKind as WireFlowFactKind, FlowVerdict as WireFlowVerdict } from '../../api/hub';
import { openLookup } from '../../core/open-lookup';
import type { ChunkDeleteRefusal, ChunkWaitKind, MigrationSource, MigrationMode, UsageKind, FlowState, FlowFactKind, FlowVerdict } from './chunk-detail.model';

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
