import type { SecretView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { referenceCount, secretBadges, secretRecordVm, secretRows } from './secrets.model';

const USED: SecretView = {
  name: 'gh-blizzard',
  revision: 3,
  created_at: '2026-01-01T00:00:00Z',
  replaced_at: '2026-01-05T00:00:00Z',
  replaced_by: 'pgross',
  references: [
    { kind: 'work_source', key: 'blizzard' },
    { kind: 'repository', key: 'blizzard' },
  ],
};
const UNUSED: SecretView = { ...USED, name: 'gh-spare', references: [] };
const RETIRED: SecretView = { ...USED, name: 'gh-old', references: [], retired: true };

describe('secretBadges', () => {
  it('badges a secret nothing refers to as unused', () => {
    expect(secretBadges(UNUSED).map((b) => b.label)).toEqual(['unused']);
    expect(secretBadges({ ...UNUSED, references: undefined }).map((b) => b.label)).toEqual(['unused']);
  });

  it('badges a retired secret as retired only', () => {
    expect(secretBadges(RETIRED).map((b) => b.label)).toEqual(['retired']);
  });

  it('badges nothing on a referenced secret', () => {
    expect(secretBadges(USED)).toEqual([]);
  });
});

describe('referenceCount', () => {
  it('counts with the right plural', () => {
    expect(referenceCount(USED)).toBe('2 references');
    expect(referenceCount({ ...USED, references: [{ kind: 'repository', key: 'x' }] })).toBe('1 reference');
    expect(referenceCount(UNUSED)).toBe('0 references');
  });
});

describe('secretRows', () => {
  it('filters by lifecycle and maps each secret', () => {
    expect(secretRows([USED, UNUSED, RETIRED], 'active').map((r) => r.key)).toEqual(['gh-blizzard', 'gh-spare']);
    expect(secretRows([USED], 'all')[0]).toEqual({
      key: 'gh-blizzard',
      title: 'gh-blizzard',
      sub: ['replaced by pgross', '2 references'],
      badges: [],
      revision: 3,
      retired: false,
    });
  });
});

describe('secretRecordVm', () => {
  it('answers null with none loaded', () => {
    expect(secretRecordVm(undefined)).toBeNull();
  });

  it('shows no value, and links every referring record', () => {
    const vm = secretRecordVm(USED)!;
    expect(vm.facts.find((f) => f.label === 'Value')?.value).toContain('never shown');
    expect(vm.links?.heading).toBe('Referred to by');
    expect(vm.links?.links).toEqual([
      { kind: 'work source', name: 'blizzard', route: ['/admin', 'work-sources', 'blizzard'] },
      { kind: 'repository', name: 'blizzard', route: ['/admin', 'repositories', 'blizzard'] },
    ]);
  });

  it('shows an unreferenced secret with no links', () => {
    expect(secretRecordVm(UNUSED)?.links?.links).toEqual([]);
  });
});
