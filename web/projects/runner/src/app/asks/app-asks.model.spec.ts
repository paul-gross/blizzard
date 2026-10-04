import { compactRef, type runnerApi } from 'fleet';

import { askedForLabel, askRows } from './app-asks.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

function ask(overrides: Partial<runnerApi.AskView> = {}): runnerApi.AskView {
  return {
    question_id: 'q_1',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    lease_id: 'lease_1',
    session_id: 'sess-1',
    asked_at: '2026-07-16T11:18:00.000Z',
    question: 'Which branch?',
    ...overrides,
  };
}

describe('askedForLabel', () => {
  it('renders the duration since the ask was raised', () => {
    expect(askedForLabel('2026-07-16T11:18:00.000Z', NOW)).toBe('42m');
  });

  it('renders an em dash for a skew-broken timestamp', () => {
    expect(askedForLabel('2026-07-16T13:00:00.000Z', NOW)).toBe('—');
  });
});

describe('askRows', () => {
  it('maps each open ask to its row in wire order', () => {
    const asks = [ask(), ask({ question_id: 'q_2', asked_at: '2026-07-16T11:59:50.000Z', question: 'Ship it?' })];
    expect(askRows(asks, NOW)).toEqual([
      {
        questionId: 'q_1',
        chunkRef: compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9'),
        askedFor: '42m',
        question: 'Which branch?',
      },
      {
        questionId: 'q_2',
        chunkRef: compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9'),
        askedFor: '10s',
        question: 'Ship it?',
      },
    ]);
  });

  it('maps no asks to no rows', () => {
    expect(askRows([], NOW)).toEqual([]);
  });
});
