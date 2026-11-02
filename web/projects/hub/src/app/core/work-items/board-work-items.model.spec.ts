import type { WorkItemsView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { mergeWorkItemBatches, WORK_ITEMS_BATCH_SIZE, withinHold, workItemBatches } from './board-work-items.model';

describe('workItemBatches', () => {
  it('names each id once, sorted, a full batch at a time', () => {
    const ids = Array.from({ length: WORK_ITEMS_BATCH_SIZE + 1 }, (_, i) => `ch_${String(i).padStart(4, '0')}`);
    const batches = workItemBatches([...ids].reverse().concat(ids[0]));

    expect(batches.map((batch) => batch.length)).toEqual([WORK_ITEMS_BATCH_SIZE, 1]);
    expect(batches.flat()).toEqual(ids);
  });

  it('makes no batch for no ids', () => {
    expect(workItemBatches([])).toEqual([]);
  });
});

describe('mergeWorkItemBatches', () => {
  it('merges answered batches by chunk id and skips one still reading', () => {
    const view: WorkItemsView = { items: [] };
    const merged = mergeWorkItemBatches([new Map([['ch_a', view]]), undefined, new Map([['ch_b', view]])]);

    expect([...merged.keys()]).toEqual(['ch_a', 'ch_b']);
  });
});

describe('withinHold', () => {
  it('holds a write younger than the hold, and nothing older or never written', () => {
    expect(withinHold(1_000, 1_500, 1_000)).toBe(true);
    expect(withinHold(1_000, 2_000, 1_000)).toBe(false);
    expect(withinHold(0, 10, 1_000)).toBe(false);
  });
});
