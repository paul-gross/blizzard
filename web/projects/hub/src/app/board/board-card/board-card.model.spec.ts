import { compactRef, type WorkItemsView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { blockedLabel, firstLine, foldTitles, stepLabel, titledCardShowsStatus, workItemDescription, workItemTitles } from './board-card.model';

const item = (fields: { title?: string; label?: string; body?: string }) => ({
  source: 'hub',
  ref: '1',
  fetched_at: 't',
  comments: [],
  ...fields,
});

describe('workItemTitles', () => {
  it('reads null while the work items are unread', () => {
    expect(workItemTitles(undefined)).toBeNull();
  });

  it('reads empty for a chunk carrying no work item', () => {
    expect(workItemTitles({ items: [] })).toEqual([]);
  });

  it('keeps pointer order, standing an untitled item in by its label and dropping one with neither', () => {
    const view: WorkItemsView = { items: [item({ title: 'First' }), item({ label: 'hub#2' }), item({}), item({ title: 'Fourth' })] };
    expect(workItemTitles(view)).toEqual(['First', 'hub#2', 'Fourth']);
  });
});

describe('workItemDescription', () => {
  it("reads a single item's first line of prose", () => {
    expect(workItemDescription({ items: [item({ title: 'T', body: 'The events table is past 40M rows.' })] })).toBe(
      'The events table is past 40M rows.',
    );
  });

  it('reads null for a group, whose titles fill the row', () => {
    expect(workItemDescription({ items: [item({ body: 'one' }), item({ body: 'two' })] })).toBeNull();
  });

  it('reads null for an item with no body, and while unread', () => {
    expect(workItemDescription({ items: [item({ title: 'T' })] })).toBeNull();
    expect(workItemDescription(undefined)).toBeNull();
  });
});

describe('firstLine', () => {
  it('skips a heading that only restates the title', () => {
    expect(firstLine('# Fix the lease\n\nA runner that drops leaves the lease open.', 'Fix the lease')).toBe(
      'A runner that drops leaves the lease open.',
    );
  });

  it('strips heading, quote, and list marks', () => {
    expect(firstLine('## Context', null)).toBe('Context');
    expect(firstLine('> quoted line', null)).toBe('quoted line');
    expect(firstLine('- [ ] a task', null)).toBe('a task');
    expect(firstLine('3. numbered', null)).toBe('numbered');
  });

  it('unwraps links, inline code, and emphasis, keeping underscores in identifiers', () => {
    expect(firstLine('See [the doc](https://x) for `occurred_at` and **bold** _x_', null)).toBe(
      'See the doc for occurred_at and bold _x_',
    );
  });

  it('drops comments, fenced code (closed or not), tables, and rules', () => {
    expect(firstLine('<!-- template\nhint -->\nReal prose.', null)).toBe('Real prose.');
    expect(firstLine('```sh\nrm -rf\n```\nAfter the fence.', null)).toBe('After the fence.');
    expect(firstLine('```sh\nnever closed', null)).toBeNull();
    expect(firstLine('| a | b |\n|---|---|\n---\nBelow the table.', null)).toBe('Below the table.');
  });

  it('reads null for a body with no prose', () => {
    expect(firstLine('\n\n', null)).toBeNull();
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

describe('titledCardShowsStatus', () => {
  it("leaves a lane's namesake status unsaid", () => {
    for (const status of ['running', 'ready', 'not_ready', 'needs_human'] as const) {
      expect(titledCardShowsStatus(status)).toBe(false);
    }
  });

  it('names a status that says more than its lane', () => {
    for (const status of ['paused', 'waiting_on_human', 'delivering'] as const) {
      expect(titledCardShowsStatus(status)).toBe(true);
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
