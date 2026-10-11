import { tabCount } from './admin-shell.model';

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
