import { describe, expect, it } from 'vitest';

import { BLOCK_TONE, blockEdge, blockFill, blockStateLabel, groupBlocks, isDenseStrip, type NodeBlock, showsNodeStrip } from './node-strip.model';

describe('groupBlocks', () => {
  const blocks = (n: number): NodeBlock[] => Array.from({ length: n }, (_, i) => ({ nodeId: `nd_${i}`, name: `n${i}`, state: { kind: 'unvisited' } }));

  it('breaks the strip into groups of ten, the last holding the rest', () => {
    expect(groupBlocks(blocks(25)).map((g) => g.length)).toEqual([10, 10, 5]);
    expect(groupBlocks(blocks(10)).map((g) => g.length)).toEqual([10]);
    expect(groupBlocks(blocks(11)).map((g) => g.length)).toEqual([10, 1]);
    expect(groupBlocks([])).toEqual([]);
  });

  it('packs the strip dense past twenty blocks', () => {
    expect(isDenseStrip(groupBlocks(blocks(20)))).toBe(false);
    expect(isDenseStrip(groupBlocks(blocks(21)))).toBe(true);
  });
});

describe('showsNodeStrip', () => {
  it('shows only in the mid-graph lanes — running, waiting on a human, needing one', () => {
    expect(showsNodeStrip({ laneStatus: { kind: 'running' }, terminal: false })).toBe(true);
    expect(showsNodeStrip({ laneStatus: { kind: 'delivering' }, terminal: false })).toBe(true);
    expect(showsNodeStrip({ laneStatus: { kind: 'waiting_on_human' }, terminal: false })).toBe(true);
    expect(showsNodeStrip({ laneStatus: { kind: 'needs_human' }, terminal: false })).toBe(true);
    expect(showsNodeStrip({ laneStatus: { kind: 'ready' }, terminal: false })).toBe(false);
    expect(showsNodeStrip({ laneStatus: { kind: 'not_ready' }, terminal: false })).toBe(false);
    expect(showsNodeStrip({ laneStatus: { kind: 'done' }, terminal: true })).toBe(false);
  });

  it('hides on a terminal chunk whatever lane its status reads as', () => {
    expect(showsNodeStrip({ laneStatus: { kind: 'stopped' }, terminal: true })).toBe(false);
    expect(showsNodeStrip({ laneStatus: { kind: 'running' }, terminal: true })).toBe(false);
  });
});

describe('blockStateLabel', () => {
  it('displays known states and the raw value of an unknown', () => {
    expect(blockStateLabel({ kind: 'current' })).toBe('current');
    expect(blockStateLabel({ kind: 'unknown', raw: 'newer-state' })).toBe('newer-state');
  });
});

describe('block tones', () => {
  it('maps passed, failed, current and unknown to a tone and leaves unvisited without one', () => {
    expect(BLOCK_TONE).toEqual({ passed: 'done', failed: 'needs', current: 'spawning', unvisited: null, unknown: 'idle' });
    expect(blockFill('passed')).toBe('var(--green)');
    expect(blockEdge('failed')).toBe('var(--red-dim)');
    expect(blockFill('current')).toBe('var(--cyan)');
    expect(blockFill('unknown')).toBe('var(--label-dim)');
    expect(blockFill('unvisited')).toBeNull();
    expect(blockEdge('unvisited')).toBeNull();
  });
});
