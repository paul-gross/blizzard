import { translateRunnerStreamEvent } from './runner-stream.translate';

describe('translateRunnerStreamEvent', () => {
  it.each([
    ['lease-changed', 'lease'],
    ['ask-changed', 'ask'],
    ['escalation-changed', 'escalation'],
    ['takeover-changed', 'takeover'],
    ['fact-changed', 'fact'],
  ])('translates %s without depending on the cause', (type, kind) => {
    expect(translateRunnerStreamEvent(type, { runner_id: 'rn_1', chunk_id: 'ch_1', cause: 'deleted' })).toEqual({
      kind, runnerId: 'rn_1', chunkId: 'ch_1',
    });
    expect(translateRunnerStreamEvent(type, { runner_id: 'rn_1', chunk_id: null })).toEqual({
      kind, runnerId: 'rn_1', chunkId: null,
    });
  });

  it('translates a host-scoped environment notification', () => {
    expect(translateRunnerStreamEvent('environment-changed', { cause: 'deleted' })).toEqual({ kind: 'environment' });
  });

  it.each([
    ['new-event', { runner_id: 'rn_1' }],
    ['lease-changed', null],
    ['lease-changed', 'not json'],
    ['lease-changed', {}],
    ['lease-changed', { runner_id: '' }],
    ['lease-changed', { runner_id: 'rn_1', chunk_id: 12 }],
    ['environment-changed', []],
  ])('retains unknown or unusable frame %s for diagnostics', (type, payload) => {
    expect(translateRunnerStreamEvent(type, payload)).toEqual({ kind: 'unknown', type, raw: payload });
  });
});
