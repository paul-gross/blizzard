import { describe, expect, it } from 'vitest';

import { eventChunkIds, eventRunnerIds, eventRunnerNames, filterUniverse } from './events-panel.model';

describe('filterUniverse', () => {
  it('is empty with at most one distinct id and no selection', () => {
    expect(filterUniverse([], null)).toEqual([]);
    expect(filterUniverse(['r1', 'r1'], null)).toEqual([]);
  });

  it('sorts the distinct ids once there are two', () => {
    expect(filterUniverse(['r2', 'r1', 'r2'], null)).toEqual(['r1', 'r2']);
  });

  it('keeps an active selection even when it alone remains', () => {
    expect(filterUniverse([], 'r1')).toEqual(['r1']);
    expect(filterUniverse(['r2'], 'r1')).toEqual(['r1', 'r2']);
  });

  it('adds no chip for an empty-string selection', () => {
    expect(filterUniverse(['r1', 'r2'], '')).toEqual(['r1', 'r2']);
    expect(filterUniverse(['r1'], '')).toEqual(['r1']);
  });
});

describe('eventRunnerIds', () => {
  it('strips falsy runner ids before building the universe', () => {
    const events = [{ runner_id: 'r2' }, { runner_id: null }, { runner_id: '' }, {}, { runner_id: 'r1' }];
    expect(eventRunnerIds(events, null)).toEqual(['r1', 'r2']);
  });

  it('keeps the selected runner', () => {
    expect(eventRunnerIds([{ runner_id: 'r1' }], 'r9')).toEqual(['r1', 'r9']);
  });
});

describe('eventRunnerNames', () => {
  it('maps each runner id to the name its events carry, skipping a nameless or runnerless event', () => {
    const names = eventRunnerNames([
      { runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3', runner_name: 'r-claude' },
      { runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M', runner_name: null },
      { runner_id: null, runner_name: 'r-orphan' },
      {},
    ]);
    expect([...names]).toEqual([['rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude']]);
  });
});

describe('eventChunkIds', () => {
  it('strips falsy chunk ids before building the universe', () => {
    const events = [{ chunk_id: 'ch_b' }, { chunk_id: null }, { chunk_id: 'ch_a' }];
    expect(eventChunkIds(events, null)).toEqual(['ch_a', 'ch_b']);
  });

  it('hides the row for a single chunk with no selection', () => {
    expect(eventChunkIds([{ chunk_id: 'ch_a' }, { chunk_id: undefined }], null)).toEqual([]);
  });
});
