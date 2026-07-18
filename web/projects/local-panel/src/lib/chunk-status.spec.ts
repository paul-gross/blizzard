import type { runnerApi } from 'fleet';

import { type MachineChunkFacts, deriveMachineChunkStatus } from './chunk-status';

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'lease_1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: 'sess-1',
    pid: 1,
    environment_id: 'e1',
    workdir: '/ws/e1',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:00.000Z',
    state: 'running',
    closed_at: null,
    closure_reason: null,
    ...overrides,
  };
}

const NO_FACTS: MachineChunkFacts = {
  escalatedChunkIds: new Set<string>(),
  takeoverChunkIds: new Set<string>(),
  askChunkIds: new Set<string>(),
};

describe('deriveMachineChunkStatus', () => {
  it('maps the plain lease states', () => {
    expect(deriveMachineChunkStatus(lease({ state: 'running' }), NO_FACTS)).toEqual({
      label: 'RUNNING',
      tone: 'running',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'stale' }), NO_FACTS)).toEqual({ label: 'STALE', tone: 'stale' });
    expect(deriveMachineChunkStatus(lease({ state: 'spawning' }), NO_FACTS)).toEqual({
      label: 'SPAWNING',
      tone: 'spawning',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'exited' }), NO_FACTS)).toEqual({ label: 'EXITED', tone: 'idle' });
  });

  it('reads a transitioned closure as the healthy done tone, other closures dim', () => {
    expect(deriveMachineChunkStatus(lease({ state: 'closed', closure_reason: 'transitioned' }), NO_FACTS)).toEqual({
      label: 'TRANSITIONED',
      tone: 'done',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'closed', closure_reason: 'failed' }), NO_FACTS)).toEqual({
      label: 'CLOSED · FAILED',
      tone: 'idle',
    });
  });

  it('an open ask outranks the lease state — the chunk is waiting on a human', () => {
    const facts = { ...NO_FACTS, askChunkIds: new Set(['ch_1']) };
    expect(deriveMachineChunkStatus(lease({ state: 'parked' }), facts)).toEqual({
      label: 'WAITING · ASK',
      tone: 'waiting',
    });
  });

  it('an escalation outranks an ask', () => {
    const facts = { ...NO_FACTS, escalatedChunkIds: new Set(['ch_1']), askChunkIds: new Set(['ch_1']) };
    expect(deriveMachineChunkStatus(lease({ state: 'closed', closure_reason: 'escalated' }), facts)).toEqual({
      label: 'NEEDS HUMAN',
      tone: 'needs',
    });
  });

  it('an open takeover outranks everything — a human is in the session now', () => {
    const facts: MachineChunkFacts = {
      escalatedChunkIds: new Set(['ch_1']),
      takeoverChunkIds: new Set(['ch_1']),
      askChunkIds: new Set(['ch_1']),
    };
    expect(deriveMachineChunkStatus(lease(), facts)).toEqual({ label: 'HUMAN IN SESSION', tone: 'takeover' });
  });

  it('facts for other chunks never leak onto this one', () => {
    const facts = { ...NO_FACTS, escalatedChunkIds: new Set(['ch_other']) };
    expect(deriveMachineChunkStatus(lease(), facts)).toEqual({ label: 'RUNNING', tone: 'running' });
  });
});
