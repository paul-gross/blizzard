import { describe, expect, it } from 'vitest';

import { eventChunkIds, eventRunnerIds, eventRunnerNames, filterUniverse } from './events-panel.model';

describe('filterUniverse', () => {
  it('retains a single distinct id with no selection', () => {
    expect(filterUniverse([], null)).toEqual([]);
    expect(filterUniverse(['r1', 'r1'], null)).toEqual(['r1']);
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
    const events = [{ runnerId: 'r2' }, { runnerId: null }, { runnerId: '' }, { runnerId: 'r1' }];
    expect(eventRunnerIds(events, null)).toEqual(['r1', 'r2']);
  });

  it('keeps the selected runner', () => {
    expect(eventRunnerIds([{ runnerId: 'r1' }], 'r9')).toEqual(['r1', 'r9']);
  });
});

describe('eventRunnerNames', () => {
  it('maps each runner id to the name its events carry, skipping a nameless or runnerless event', () => {
    const names = eventRunnerNames([
      { runnerId: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3', runnerName: 'r-claude' },
      { runnerId: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M', runnerName: null },
      { runnerId: null, runnerName: 'r-orphan' },
    ]);
    expect([...names]).toEqual([['rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude']]);
  });
});

describe('eventChunkIds', () => {
  it('strips falsy chunk ids before building the universe', () => {
    const events = [{ chunkId: 'ch_b' }, { chunkId: null }, { chunkId: 'ch_a' }];
    expect(eventChunkIds(events, null)).toEqual(['ch_a', 'ch_b']);
  });

  it('offers a single chunk alongside chunkless events', () => {
    expect(eventChunkIds([{ chunkId: 'ch_a' }, { chunkId: null }], null)).toEqual(['ch_a']);
  });
});
