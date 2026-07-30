import { asyncState, asyncStateOf, type AsyncStateQuery } from './query-state';

function query(overrides: Partial<{ pending: boolean; error: boolean }> = {}): AsyncStateQuery {
  const pending = overrides.pending ?? false;
  const error = overrides.error ?? false;
  return {
    isPending: () => pending,
    isError: () => error,
  };
}

describe('asyncState', () => {
  it('maps a pending query to loading regardless of emptiness', () => {
    expect(asyncState(query({ pending: true }), true)).toBe('loading');
    expect(asyncState(query({ pending: true }), false)).toBe('loading');
  });

  it('maps an errored, non-pending query to error', () => {
    expect(asyncState(query({ error: true }), true)).toBe('error');
  });

  it('maps a settled query to empty or ready by isEmpty', () => {
    expect(asyncState(query(), true)).toBe('empty');
    expect(asyncState(query(), false)).toBe('ready');
  });

  it('stays ready when data is present and a background refetch is in flight (AC 6)', () => {
    // A background refetch never sets isPending() back to true (TanStack only
    // reports `pending` while there is no data at all) — this asserts the
    // helper reads isPending(), not isFetching(), so it never regresses.
    const backgroundRefetch = query({ pending: false, error: false });
    expect(asyncState(backgroundRefetch, false)).toBe('ready');
  });
});

describe('asyncStateOf', () => {
  it('is loading if any query is pending', () => {
    expect(asyncStateOf([query(), query({ pending: true })], false)).toBe('loading');
  });

  it('is error if any query errored and none are pending', () => {
    expect(asyncStateOf([query(), query({ error: true })], false)).toBe('error');
  });

  it('falls back to empty/ready when every query has settled', () => {
    expect(asyncStateOf([query(), query()], true)).toBe('empty');
    expect(asyncStateOf([query(), query()], false)).toBe('ready');
  });
});
