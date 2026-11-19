import type { ForgeSummary } from 'fleet';
import { lifecycleTone } from '../../core/lifecycle-tone';
import { forge, forgeRecord } from '../../forge/forge.builders';
import { budgetFacts, FORGE_FIELD_VIEWS, forgeOptions, forgeRecordVm, forgeRows } from './forges.model';

const GITHUB: ForgeSummary = {
  forge_id: 'frg_1', slug: 'github', name: 'GitHub', kind: 'github', api_base: 'https://api.github.com',
  web_base: null, web_origin: 'https://github.com', revision: 2, retired: false,
  created_at: '2026-01-01T00:00:00Z', created_by: 'usr_1', created_by_name: 'Ana',
};
const GITLAB: ForgeSummary = { ...GITHUB, forge_id: 'frg_2', slug: 'gitlab', kind: 'gitlab', name: 'GitLab' };
const RETIRED: ForgeSummary = { ...GITHUB, forge_id: 'frg_3', slug: 'old', retired: true };

describe('forge admin models', () => {
  it('filters lifecycle and shows slug, name and kind', () => {
    const rows = [forge(), forge({ id: 'frg_3', slug: 'old', retired: true })];
    expect(forgeRows(rows, 'active')).toMatchObject([
      { key: 'frg_1', title: 'github', sub: ['GitHub', 'github', 'https://api.github.com'] },
    ]);
    expect(forgeRows(rows, 'retired')[0].badges).toEqual([{ label: 'retired', tone: lifecycleTone(true) }]);
  });

  it('draws kind on create only, in the order and labels the legacy list had', () => {
    expect(FORGE_FIELD_VIEWS.map((field) => [field.key, field.label])).toEqual([
      ['slug', 'Slug'], ['name', 'Name'], ['kind', 'Kind'], ['apiBase', 'API base'], ['webBase', 'Web base'],
    ]);
    expect(FORGE_FIELD_VIEWS.filter((field) => field.createOnly).map((field) => field.key)).toEqual(['kind']);
  });

  it('lists only enabled forges, matching a source provider when requested', () => {
    expect(forgeOptions([GITHUB, GITLAB, RETIRED]).map((option) => option.value)).toEqual(['frg_1', 'frg_2']);
    expect(forgeOptions([GITHUB, GITLAB, RETIRED], 'github')).toEqual([{ value: 'frg_1', label: 'github · GitHub', kind: 'github' }]);
  });

  it('links both kinds of referring records by id and shows the effective web origin', () => {
    const vm = forgeRecordVm(forgeRecord({
      repositories: [{ key: 'repo_1', label: 'blizzard' }],
      workSources: [{ key: 'wsrc_1', label: 'issues' }],
    }))!;
    expect(vm.facts.find((fact) => fact.label === 'Web origin')?.value).toBe('https://github.com');
    expect(vm.links?.links).toEqual([
      { kind: 'repository', name: 'blizzard', route: ['/admin', 'repositories', 'repo_1'] },
      { kind: 'work source', name: 'issues', route: ['/admin', 'work-sources', 'wsrc_1'] },
    ]);
  });

  it('shows the capabilities it offers and one fact per budget reading', () => {
    const detail = forgeRecord({
      offered: ['checks', 'merge'],
      budgets: [
        { bucket: 'core', secretName: 'gh', remaining: 4000, limit: 5000, reset: new Date(2026, 10, 1, 12, 0, 0).toISOString() },
        { bucket: 'graphql', secretName: null, remaining: 10, limit: 100, reset: new Date(2026, 10, 1, 13, 0, 0).toISOString() },
      ],
    });
    const facts = forgeRecordVm(detail)!.facts.map((fact) => [fact.label, fact.value]);
    expect(facts).toContainEqual(['Capabilities', 'checks, merge']);
    expect(facts).toContainEqual(['Budget core · gh', '4000/5000 remaining, resets 2026/11/01 12:00:00']);
    expect(facts).toContainEqual(['Budget graphql', '10/100 remaining, resets 2026/11/01 13:00:00']);
  });

  it('reads none for no capabilities and no reading yet for no budgets', () => {
    const facts = forgeRecordVm(forgeRecord())!.facts.map((fact) => [fact.label, fact.value]);
    expect(facts).toContainEqual(['Capabilities', 'none']);
    expect(budgetFacts([])).toEqual([{ label: 'Budgets', value: 'no reading yet' }]);
  });

  it('shows an unparseable budget reset verbatim', () => {
    expect(budgetFacts([{ bucket: 'core', secretName: null, remaining: 1, limit: 2, reset: 'bad reset' }])[0].value)
      .toBe('1/2 remaining, resets bad reset');
  });
});
