import { compactRef } from 'fleet';
import { workItemEntry, workRefView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { blockedLabel, cardShowsStatus, finishedWord, foldTitles, stepLabel, workLines } from './board-card.model';

const item = (fields: { title?: string; label?: string; body?: string; web_url?: string }) =>
  workItemEntry({ source: 'hub', ref: '1', fetched_at: 't', label: null, title: null, body: null, web_url: null, ...fields });

describe('workLines', () => {
  const at = (ref: string, fields: { title?: string; label?: string; web_url?: string } = {}) => workItemEntry({ ...item(fields), ref });

  it('pairs each labeled pointer with its own item, matched by source and ref rather than position', () => {
    const refs = [
      workRefView({ source: 'hub', ref: '1', label: 'hub#1', web_url: 'https://example.test/1' }),
      workRefView({ source: 'hub', ref: '2', label: 'hub#2', web_url: null }),
    ];
    const view = { items: [at('2', { title: 'Second', web_url: 'https://example.test/2' }), at('1', { title: 'First' })] };
    expect(workLines(refs, view)).toEqual([
      { label: 'hub#1', title: 'First', url: 'https://example.test/1' },
      { label: 'hub#2', title: 'Second', url: 'https://example.test/2' },
    ]);
  });

  it("leaves a read item's missing title null rather than repeating the label it sits under", () => {
    const view = { items: [at('1', { label: 'hub#1' })] };
    expect(workLines([workRefView({ source: 'hub', ref: '1', label: 'hub#1', web_url: null })], view)).toEqual([{ label: 'hub#1', title: null, url: null }]);
  });

  it('holds every title null while the items are unread', () => {
    expect(workLines([workRefView({ source: 'hub', ref: '1', label: 'hub#1', web_url: null })], undefined)).toEqual([{ label: 'hub#1', title: null, url: null }]);
  });

  it('holds an empty line for an unlabeled pointer until read, then names its item by title', () => {
    const refs = [workRefView({ source: 'hub', ref: '9', label: null, web_url: null })];
    expect(workLines(refs, undefined)).toEqual([{ label: null, title: null, url: null }]);
    expect(workLines(refs, { items: [at('9', { title: 'Orphan' })] })).toEqual([{ label: null, title: 'Orphan', url: null }]);
  });

  it('drops an unlabeled pointer whose item has nothing to name it by, and names an item read under no pointer', () => {
    const view = { items: [at('9', {}), at('5', { title: 'Unpointed' })] };
    expect(workLines([workRefView({ source: 'hub', ref: '9', label: null, web_url: null })], view)).toEqual([{ label: null, title: 'Unpointed', url: null }]);
  });
});

describe('foldTitles', () => {
  const titles = (n: number) => Array.from({ length: n }, (_, i) => `t${i}`);

  it('shows up to three titles unfolded', () => {
    expect(foldTitles(titles(3), false)).toEqual({ shown: titles(3), hidden: 0 });
  });

  it('folds four or more to the first two', () => {
    expect(foldTitles(titles(4), false)).toEqual({ shown: titles(2), hidden: 2 });
    expect(foldTitles(titles(10), false).hidden).toBe(8);
  });

  it('shows every title once opened', () => {
    expect(foldTitles(titles(10), true)).toEqual({ shown: titles(10), hidden: 0 });
  });
});

describe('cardShowsStatus', () => {
  it("leaves a lane's namesake status unsaid", () => {
    for (const status of ['running', 'ready', 'not_ready', 'needs_human'] as const) {
      expect(cardShowsStatus(status)).toBe(false);
    }
  });

  it('names a status that says more than its lane', () => {
    for (const status of ['paused', 'waiting_on_human', 'delivering'] as const) {
      expect(cardShowsStatus(status)).toBe(true);
    }
  });
});

describe('stepLabel', () => {
  it("reads the node's name, or stopped for a stopped chunk", () => {
    expect(stepLabel('running', 'build')).toBe('build');
    expect(stepLabel('stopped', 'deliver')).toBe('stopped');
  });
});

describe('blockedLabel', () => {
  it('names one prerequisite by compact ref, and counts several', () => {
    expect(blockedLabel('ch_01abcdefghijklmnopqrstuvwx', 1)).toBe(compactRef('ch_01abcdefghijklmnopqrstuvwx'));
    expect(blockedLabel('ch_01abcdefghijklmnopqrstuvwx', 3)).toBe('3 chunks');
  });
});

describe('finishedWord', () => {
  it('names a stopped chunk Stopped and every other finish Completed', () => {
    expect(finishedWord('stopped')).toBe('Stopped');
    expect(finishedWord('done')).toBe('Completed');
  });
});
