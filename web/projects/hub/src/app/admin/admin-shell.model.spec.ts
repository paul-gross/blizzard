import { changesTotal, tabCount } from './admin-shell.model';

describe('tabCount', () => {
  it('counts every record, or only those the lensed project links', () => {
    const records = [{ name: 'hub' }, { name: 'jira' }, { name: 'gh' }];
    expect(tabCount(records)).toBe(3);
    expect(tabCount(records, new Set(['jira', 'hub']))).toBe(2);
    expect(tabCount(records, new Set())).toBe(0);
  });

  it('shows no count before the read answers', () => {
    expect(tabCount(undefined)).toBeNull();
    expect(tabCount(undefined, new Set(['jira']))).toBeNull();
  });
});

describe('changesTotal', () => {
  it("counts every change the log's filters select, not only the first page's", () => {
    expect(changesTotal([{ changes: [], total: 120, next_before: 9 }, { changes: [], total: 120, next_before: null }])).toBe(120);
    expect(changesTotal([{ changes: [], total: 0, next_before: null }])).toBe(0);
  });

  it('shows no count before the first page answers', () => {
    expect(changesTotal(undefined)).toBeNull();
    expect(changesTotal([])).toBeNull();
  });
});
