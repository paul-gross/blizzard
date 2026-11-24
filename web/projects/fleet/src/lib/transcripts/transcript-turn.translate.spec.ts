import type { TurnSegmentViewOutput } from '../api/hub';
import { translateTranscriptTurn } from './transcript-turn.translate';

function turn(overrides: Partial<TurnSegmentViewOutput> = {}): TurnSegmentViewOutput {
  return {
    index: 0, kind: 'asst', text: '', timestamp: null, thinking_redacted: false,
    truncated: false, tool: null, sidechain: null, ...overrides,
  };
}

describe('translateTranscriptTurn', () => {
  it.each(['env', 'asst', 'tool', 'thinking', 'sidechain'] as const)('preserves the known %s kind', (kind) => {
    expect(translateTranscriptTurn(turn({ kind })).kind).toBe(kind);
  });

  it('preserves nested turns, tool patches and partial sidechain metadata', () => {
    const source = turn({
      kind: 'tool',
      tool: { name: 'Task', tool_use_id: 't', input: {}, input_shape: 'object', input_unparsed: null,
        output: null, output_truncated: true, output_patch: true },
      sidechain: { agent_id: null, agent_type: null, link: 'unlinked', turns: [turn({ index: 4, text: 'nested' })] },
    });
    const translated = translateTranscriptTurn(source);
    expect(translated).toEqual(source);
    expect(translated.sidechain?.turns[0]).not.toBe(source.sidechain?.turns[0]);
  });

  it('preserves empty and unlisted turns', () => {
    const source = turn({ kind: 'future' as TurnSegmentViewOutput['kind'] });
    expect(translateTranscriptTurn(source)).toEqual({ ...source, kind: 'unknown', rawKind: 'future' });
  });

  it('translates unknown kinds in nested sidechains', () => {
    const nested = turn({ kind: 'new-child' as TurnSegmentViewOutput['kind'] });
    const source = turn({ kind: 'sidechain', sidechain: {
      agent_id: null, agent_type: null, link: 'unlinked', turns: [nested],
    } });
    expect(translateTranscriptTurn(source).sidechain?.turns[0]).toEqual({
      ...nested, kind: 'unknown', rawKind: 'new-child',
    });
  });
});
