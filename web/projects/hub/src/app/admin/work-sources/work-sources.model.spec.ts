import { describe, expect, it } from 'vitest';

import { builtInTone, lifecycleTone } from '../../core/lifecycle-tone';
import { builtInWorkSource, workSource } from './work-source.builders';
import type { WorkSource } from './work-source.domain';
import {
  workSourceActions,
  workSourceBadges,
  workSourceCliName,
  workSourceFieldViews,
  workSourceRecordVm,
  workSourceRows,
} from './work-sources.model';

const WINTER = workSource({
  id: 'wsrc_w',
  slug: 'winter',
  name: 'winter',
  provider: 'github',
  locator: 'paul-gross/winter',
  forgeId: 'frg_github',
  forgeSlug: 'github',
  forgeName: 'GitHub',
  credentialId: 'cred_w',
  credentialSlug: 'gh-winter',
  annotate: true,
  edit: false,
  revision: 2,
  createdBy: 'pgross',
});
const OLD = workSource({ ...WINTER, id: 'wsrc_o', slug: 'old', name: 'Old one', annotate: false, retired: true, revision: 3 });
const HUB = builtInWorkSource({ slug: 'hub' });

describe('workSourceBadges', () => {
  it('badges the built-in source and a retired one', () => {
    expect(workSourceBadges(HUB)).toEqual([{ label: 'built-in', tone: builtInTone }]);
    expect(workSourceBadges(OLD)).toEqual([{ label: 'retired', tone: lifecycleTone(true) }]);
    expect(workSourceBadges(WINTER)).toEqual([]);
  });
});

describe('workSourceRows', () => {
  it('filters by lifecycle and maps each source', () => {
    const rows = workSourceRows([WINTER, OLD, HUB], 'active');
    expect(rows.map((r) => r.key)).toEqual(['wsrc_w', 'hub']);
    expect(rows[0]).toEqual({
      key: 'wsrc_w',
      title: 'winter',
      sub: ['github', 'paul-gross/winter', 'annotates'],
      badges: [],
      revision: 2,
      retired: false,
    });
    expect(rows[1].revision).toBeNull();
  });

  it('shows only retired sources under retired', () => {
    expect(workSourceRows([WINTER, OLD], 'retired').map((r) => r.key)).toEqual(['wsrc_o']);
  });

  it('titles a row by slug and shows a name that differs from it', () => {
    expect(workSourceRows([OLD], 'all')[0]).toMatchObject({ title: 'old', sub: ['Old one', 'github', 'paul-gross/winter'] });
  });
});

describe('workSourceRecordVm', () => {
  it('answers null with no source loaded', () => {
    expect(workSourceRecordVm(undefined)).toBeNull();
  });

  it('shows a configured source with its fields, credential link, and history', () => {
    const vm = workSourceRecordVm(WINTER)!;
    expect(vm.facts.find((f) => f.label === 'Provider')?.value).toBe('github');
    expect(vm.facts.find((f) => f.label === 'Forge')?.value).toBe('GitHub');
    expect(vm.facts.find((f) => f.label === 'Annotates')?.value).toBe('yes');
    expect(vm.revision).toBe(2);
    expect(vm.links?.links).toEqual([
      { kind: 'forge', name: 'github', route: ['/admin', 'forges', 'frg_github'] },
      { kind: 'credential', name: 'gh-winter', route: ['/admin', 'credentials', 'cred_w'] },
    ]);
    expect(vm.hasHistory).toBe(true);
    expect(vm.note).toBeNull();
  });

  it('shows who created it by name, falling back to the id', () => {
    const createdBy = (source: WorkSource) => workSourceRecordVm(source)!.facts.find((f) => f.label === 'Created by')?.value;
    expect(createdBy({ ...WINTER, createdBy: 'usr_1', createdByName: 'Paul Gross' })).toBe('Paul Gross');
    expect(createdBy({ ...WINTER, createdBy: 'usr_1' })).toBe('usr_1');
  });

  it('titles the record by slug and shows the display name as a fact', () => {
    const vm = workSourceRecordVm(OLD)!;
    expect(vm.name).toBe('old');
    expect(vm.facts[0]).toEqual({ label: 'Name', value: 'Old one' });
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

describe('workSourceCliName', () => {
  it('names a configured source by its slug, and the built-in one not at all', () => {
    expect(workSourceCliName(OLD)).toBe('old');
    expect(workSourceCliName(HUB)).toBeNull();
    expect(workSourceCliName(undefined)).toBeNull();
  });
});

describe('workSourceActions', () => {
  it('offers retire to an editor on a desktop, enable once retired, and nothing for the built-in source', () => {
    expect(workSourceActions('desktop', true, WINTER)).toMatchObject({ edit: true, lifecycle: 'retire' });
    expect(workSourceActions('desktop', true, OLD)).toMatchObject({ lifecycle: 'enable' });
    expect(workSourceActions('desktop', true, HUB)).toBeNull();
    expect(workSourceActions('desktop', false, WINTER)).toBeNull();
    expect(workSourceActions('mobile', true, WINTER)).toBeNull();
  });
});

describe('workSourceFieldViews', () => {
  const forges = [{ value: 'frg_github', label: 'github', kind: 'github' }];
  const credentials = [{ value: 'cred_w', label: 'gh-winter', kind: 'frg_github' }];

  it('draws the forge and credential from the given choices, each hanging on its parent', () => {
    const fields = workSourceFieldViews(forges, credentials);
    expect(fields.map((f) => f.key)).toEqual(['slug', 'name', 'provider', 'locator', 'forge', 'credential', 'annotate']);
    expect(fields.find((f) => f.key === 'forge')).toMatchObject({ options: forges, filterBy: 'provider' });
    expect(fields.find((f) => f.key === 'credential')).toMatchObject({ options: credentials, filterBy: 'forge' });
  });
});
