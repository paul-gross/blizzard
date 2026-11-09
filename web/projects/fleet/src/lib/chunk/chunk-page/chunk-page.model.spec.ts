import { boardBackParams, chunkProjectColor, chunkTabOptions, nextFocus, preDetailState } from './chunk-page.model';

describe('chunkTabOptions', () => {
  it('offers the Transcripts tab last when the identity may read transcripts', () => {
    expect(chunkTabOptions(true).map((o) => o.value)).toEqual(['general', 'node-history', 'artifacts', 'transcripts']);
  });

  it('leaves the Transcripts tab off otherwise', () => {
    expect(chunkTabOptions(false).map((o) => o.value)).toEqual(['general', 'node-history', 'artifacts']);
  });
});

describe('preDetailState', () => {
  it('is error for a failed read and loading otherwise', () => {
    expect(preDetailState(true)).toBe('error');
    expect(preDetailState(false)).toBe('loading');
  });
});

describe('nextFocus', () => {
  it('focuses the detail back control and remembers the opened row', () => {
    expect(nextFocus('s2', null, 'data-step-key', 'node-history-back')).toEqual({
      request: { kind: 'detail', testid: 'node-history-back' },
      origin: 's2',
    });
  });

  it('returns to the row the detail was opened from', () => {
    expect(nextFocus(null, 's2', 'data-step-key', 'node-history-back')).toEqual({
      request: { kind: 'list', attribute: 'data-step-key', value: 's2' },
      origin: 's2',
    });
  });

  it('falls back to the tab when closing a detail that was deep-linked', () => {
    expect(nextFocus(null, null, 'data-step-key', 'node-history-back')).toEqual({
      request: { kind: 'tab' },
      origin: null,
    });
  });
});

describe('chunkProjectColor', () => {
  const lookup = (projectId: string): string | null => (projectId === 'p1' ? '#123456' : null);

  it("reads the project's color through the lookup", () => {
    expect(chunkProjectColor('p1', lookup)).toBe('#123456');
  });

  it('is null for an unknown project, a chunk with no project, or a daemon with no lookup', () => {
    expect(chunkProjectColor('p2', lookup)).toBeNull();
    expect(chunkProjectColor(null, lookup)).toBeNull();
    expect(chunkProjectColor(undefined, lookup)).toBeNull();
    expect(chunkProjectColor('p1', undefined)).toBeNull();
  });
});

describe('boardBackParams', () => {
  const query = (params: Record<string, string>) => ({ get: (name: string) => params[name] ?? null });

  it('returns to the board with the chunk selected, in the layout it was opened from', () => {
    expect(boardBackParams('ch_1', query({ layout: 'tracks' }))).toEqual({
      chunk: 'ch_1',
      layout: 'tracks',
      step: null,
      at: null,
    });
  });

  it("returns to the step's session as the board's ?step", () => {
    expect(boardBackParams('ch_1', query({ from: 'lease_1', fromAt: '2026-11-01T00:00:00Z' }))).toMatchObject({
      step: 'lease_1',
      at: null,
    });
  });

  it("returns to the step's instant as the board's ?at when no session names it", () => {
    expect(boardBackParams('ch_1', query({ fromAt: '2026-11-01T00:00:00Z' }))).toMatchObject({
      step: null,
      at: '2026-11-01T00:00:00Z',
    });
  });
});
