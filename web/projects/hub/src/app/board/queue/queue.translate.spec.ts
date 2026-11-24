import { backlogPeekEntry, queuePeekEntry } from 'fleet/testing';
import { fromBoardOrderEntry } from './queue.translate';

describe('fromBoardOrderEntry', () => {
  it('keeps the chunk identity from either server-ordered list', () => {
    expect(fromBoardOrderEntry(backlogPeekEntry({ chunk_id: 'ch_backlog', position: 0 }))).toEqual({ chunk_id: 'ch_backlog' });
    expect(fromBoardOrderEntry(queuePeekEntry({ chunk_id: 'ch_ready', position: 0 }))).toEqual({ chunk_id: 'ch_ready' });
  });
});
