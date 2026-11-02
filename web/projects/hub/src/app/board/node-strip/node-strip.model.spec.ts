import { describe, expect, it } from 'vitest';

import { groupBlocks, isDenseStrip, type NodeBlock, showsNodeStrip } from './node-strip.model';

describe('groupBlocks', () => {
  const blocks = (n: number): NodeBlock[] => Array.from({ length: n }, (_, i) => ({ node_id: `nd_${i}`, name: `n${i}`, state: 'unvisited' }));

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
    expect(showsNodeStrip({ status: 'running', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'delivering', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'waiting_on_human', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'needs_human', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'ready', terminal: false })).toBe(false);
    expect(showsNodeStrip({ status: 'not_ready', terminal: false })).toBe(false);
    expect(showsNodeStrip({ status: 'done', terminal: true })).toBe(false);
  });

  it('hides on a terminal chunk whatever lane its status reads as', () => {
    expect(showsNodeStrip({ status: 'stopped', terminal: true })).toBe(false);
    expect(showsNodeStrip({ status: 'running', terminal: true })).toBe(false);
  });
});
