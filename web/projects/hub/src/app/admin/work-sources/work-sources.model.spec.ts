import type { WorkSourceSummary } from 'fleet';
import { describe, expect, it } from 'vitest';

import { builtInTone, lifecycleTone } from '../../core/lifecycle-tone';
import {
  workSourceBadges,
  workSourceCliName,
  workSourceCreate,
  workSourceRecordVm,
  workSourceRows,
} from './work-sources.model';

const WINTER: WorkSourceSummary = {
  forge_id: 'frg_github',
  forge_slug: 'github',
  forge_name: 'GitHub',
  warnings: [],
  work_source_id: 'wsrc_w',
  slug: 'winter',
  name: 'winter',
  provider: 'github',
  locator: 'paul-gross/winter',
  annotate: true,
  edit: false,
  secret: 'gh-winter',
  revision: 2,
  created_by: 'pgross',
  api_base: null,
  built_in: false,
  created_at: null,
  created_by_name: null,
  retired: false,
  web_base: null,
};
const OLD: WorkSourceSummary = { ...WINTER, work_source_id: 'wsrc_o', slug: 'old', name: 'Old one', annotate: false, retired: true, revision: 3 };
const HUB: WorkSourceSummary = { forge_id: null, forge_slug: null, forge_name: null, warnings: [], name: 'hub', slug: 'hub', work_source_id: null, annotate: false, edit: true, built_in: true, api_base: null, created_at: null, created_by: null, created_by_name: null, locator: null, provider: null, retired: false, revision: null, secret: null, web_base: null };

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

  it('shows a configured source with its fields, token link, and history', () => {
    const vm = workSourceRecordVm(WINTER)!;
    expect(vm.facts.find((f) => f.label === 'Provider')?.value).toBe('github');
    expect(vm.facts.find((f) => f.label === 'Forge')?.value).toBe('GitHub');
    expect(vm.facts.find((f) => f.label === 'Annotates')?.value).toBe('yes');
    expect(vm.revision).toBe(2);
    expect(vm.links?.links).toEqual([
      { kind: 'forge', name: 'github', route: ['/admin', 'forges', 'frg_github'] },
      { kind: 'secret', name: 'gh-winter', route: ['/admin', 'secrets', 'gh-winter'] },
    ]);
    expect(vm.hasHistory).toBe(true);
    expect(vm.note).toBeNull();
  });

  it('shows who created it by name, falling back to the id', () => {
    const createdBy = (source: WorkSourceSummary) => workSourceRecordVm(source)!.facts.find((f) => f.label === 'Created by')?.value;
    expect(createdBy({ ...WINTER, created_by: 'usr_1', created_by_name: 'Paul Gross' })).toBe('Paul Gross');
    expect(createdBy({ ...WINTER, created_by: 'usr_1' })).toBe('usr_1');
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

  it('creates a work source from the form, carrying the checkbox as a boolean and leaving empty text out', () => {
    const body = { slug: 'winter', name: '', provider: 'github', locator: 'paul-gross/winter', forge: 'frg_github', secret: '', annotate: true };
    expect(workSourceCreate(body)).toEqual({
      slug: 'winter',
      provider: 'github',
      locator: 'paul-gross/winter',
      forge: 'frg_github',
      annotate: true,
    });
    expect(workSourceCreate({ ...body, annotate: false, secret: 'gh-winter' })).toMatchObject({
      annotate: false,
      secret: 'gh-winter',
    });
  });
});
