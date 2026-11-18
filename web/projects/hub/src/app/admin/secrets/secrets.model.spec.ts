import { describe, expect, it } from 'vitest';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { secret } from './secret.builders';
import type { Secret } from './secret.domain';
import {
  referenceCount,
  SECRET_CREATE_FIELD_VIEWS,
  SECRET_REPLACE_FIELD_VIEWS,
  secretBadges,
  secretRecordVm,
  secretRows,
} from './secrets.model';

const USED: Secret = secret();
const UNUSED: Secret = secret({ name: 'gh-spare', references: [] });
const RETIRED: Secret = secret({ name: 'gh-old', references: [], retired: true });

describe('secretBadges', () => {
  it('badges a secret nothing refers to as unused', () => {
    expect(secretBadges(UNUSED).map((b) => b.label)).toEqual(['unused']);
    expect(secretBadges({ ...UNUSED, references: [] }).map((b) => b.label)).toEqual(['unused']);
  });

  it('badges a retired secret as retired only', () => {
    expect(secretBadges(RETIRED)).toEqual([{ label: 'retired', tone: lifecycleTone(true) }]);
  });

  it('badges nothing on a referenced secret', () => {
    expect(secretBadges(USED)).toEqual([]);
  });
});

describe('referenceCount', () => {
  it('counts with the right plural', () => {
    expect(referenceCount(USED)).toBe('2 references');
    expect(referenceCount({ ...USED, references: [{ kind: 'repository', key: 'x', label: null }] })).toBe('1 reference');
    expect(referenceCount(UNUSED)).toBe('0 references');
  });
});

describe('secretRows', () => {
  it('filters by lifecycle and maps each secret', () => {
    expect(secretRows([USED, UNUSED, RETIRED], 'active').map((r) => r.key)).toEqual(['gh-blizzard', 'gh-spare']);
    expect(secretRows([USED], 'all')[0]).toEqual({
      key: 'gh-blizzard',
      title: 'gh-blizzard',
      sub: ['2 references'],
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

  it('shows who replaced it by name, falling back to the id', () => {
    const replacedBy = (secret: Secret) => secretRecordVm(secret)!.facts.find((f) => f.label === 'Replaced by')?.value;
    expect(replacedBy({ ...USED, replacedByName: 'Paul Gross' })).toBe('Paul Gross');
    expect(replacedBy(USED)).toBe('pgross');
  });

  it('shows an unreferenced secret with no links', () => {
    expect(secretRecordVm(UNUSED)?.links?.links).toEqual([]);
  });

  it('takes the value as masked multi-line text on create and replace', () => {
    expect(SECRET_CREATE_FIELD_VIEWS.find((field) => field.key === 'value')?.kind).toBe('secret-text');
    expect(SECRET_REPLACE_FIELD_VIEWS.map((field) => field.kind)).toEqual(['secret-text']);
  });
});
