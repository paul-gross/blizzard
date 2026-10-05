import type { WorkSourceSummary } from 'fleet';
import { describe, expect, it } from 'vitest';

import { workSourceBadges, workSourceRecordVm, workSourceRows } from './work-sources.model';

const WINTER: WorkSourceSummary = {
  name: 'winter',
  provider: 'github',
  locator: 'paul-gross/winter',
  annotate: true,
  edit: false,
  secret: 'gh-winter',
  revision: 2,
  created_by: 'pgross',
};
const OLD: WorkSourceSummary = { ...WINTER, name: 'old', annotate: false, retired: true, revision: 3 };
const HUB: WorkSourceSummary = { name: 'hub', annotate: false, edit: true, built_in: true };

describe('workSourceBadges', () => {
  it('badges the built-in source and a retired one', () => {
    expect(workSourceBadges(HUB).map((b) => b.label)).toEqual(['built-in']);
    expect(workSourceBadges(OLD).map((b) => b.label)).toEqual(['retired']);
    expect(workSourceBadges(WINTER)).toEqual([]);
  });
});

describe('workSourceRows', () => {
  it('filters by lifecycle and maps each source', () => {
    const rows = workSourceRows([WINTER, OLD, HUB], 'active');
    expect(rows.map((r) => r.key)).toEqual(['winter', 'hub']);
    expect(rows[0]).toEqual({
      key: 'winter',
      title: 'winter',
      sub: ['github', 'paul-gross/winter', 'annotates'],
      badges: [],
      revision: 2,
      retired: false,
    });
    expect(rows[1].revision).toBeNull();
  });

  it('shows only retired sources under retired', () => {
    expect(workSourceRows([WINTER, OLD], 'retired').map((r) => r.key)).toEqual(['old']);
  });
});

describe('workSourceRecordVm', () => {
  it('answers null with no source loaded', () => {
    expect(workSourceRecordVm(undefined)).toBeNull();
  });

  it('shows a configured source with its fields, token link, and history', () => {
    const vm = workSourceRecordVm(WINTER)!;
    expect(vm.facts.find((f) => f.label === 'Provider')?.value).toBe('github');
    expect(vm.facts.find((f) => f.label === 'API base')?.value).toBe('—');
    expect(vm.facts.find((f) => f.label === 'Annotates')?.value).toBe('yes');
    expect(vm.revision).toBe(2);
    expect(vm.links?.links).toEqual([{ kind: 'secret', name: 'gh-winter', route: ['/admin', 'secrets', 'gh-winter'] }]);
    expect(vm.hasHistory).toBe(true);
    expect(vm.note).toBeNull();
  });

  it('notes a retired source', () => {
    expect(workSourceRecordVm(OLD)?.note).toContain('Retired');
  });

  it('shows the built-in source with no fields, revision, token, or history', () => {
    const vm = workSourceRecordVm(HUB)!;
    expect(vm.badges.map((b) => b.label)).toEqual(['built-in']);
    expect(vm.facts).toEqual([]);
    expect(vm.revision).toBeNull();
    expect(vm.links).toBeNull();
    expect(vm.hasHistory).toBe(false);
  });
});
