import type { ForgeDetail, ForgeSummary } from 'fleet';
import { FORGE_FIELDS, forgeCreate, forgeOptions, forgePickerFields, forgeRecordVm, forgeRows } from './forges.model';

const GITHUB: ForgeSummary = {
  forge_id: 'frg_1', slug: 'github', name: 'GitHub', kind: 'github', api_base: 'https://api.github.com',
  web_base: null, web_origin: 'https://github.com', revision: 2, retired: false,
  created_at: '2026-01-01T00:00:00Z', created_by: 'usr_1', created_by_name: 'Ana',
};
const GITLAB: ForgeSummary = { ...GITHUB, forge_id: 'frg_2', slug: 'gitlab', kind: 'gitlab', name: 'GitLab' };
const RETIRED: ForgeSummary = { ...GITHUB, forge_id: 'frg_3', slug: 'old', retired: true };

describe('forge admin models', () => {
  it('filters lifecycle and shows slug, name and kind', () => {
    expect(forgeRows([GITHUB, RETIRED], 'active')).toMatchObject([
      { key: 'frg_1', title: 'github', sub: ['GitHub', 'github', 'https://api.github.com'] },
    ]);
    expect(forgeRows([GITHUB, RETIRED], 'retired')[0].badges).toEqual([{ label: 'retired', tone: 'idle' }]);
  });

  it('omits optional empty create fields and leaves kind immutable on edit', () => {
    expect(forgeCreate({ slug: 'github', kind: 'github', api_base: 'https://api.github.com', name: '', web_base: '' }))
      .toEqual({ slug: 'github', kind: 'github', api_base: 'https://api.github.com' });
    expect(FORGE_FIELDS.find((field) => field.key === 'kind')?.createOnly).toBe(true);
  });

  it('lists only enabled forges, matching a source provider when requested', () => {
    expect(forgeOptions([GITHUB, GITLAB, RETIRED]).map((option) => option.value)).toEqual(['frg_1', 'frg_2']);
    expect(forgeOptions([GITHUB, GITLAB, RETIRED], 'github')).toEqual([{ value: 'frg_1', label: 'github · GitHub', kind: 'github' }]);
    expect(forgePickerFields([{ key: 'forge', label: 'Forge', kind: 'select' }], [GITHUB, GITLAB])[0].options)
      .toEqual([{ value: 'frg_1', label: 'github · GitHub', kind: 'github' }, { value: 'frg_2', label: 'gitlab · GitLab', kind: 'gitlab' }]);
  });

  it('links both kinds of referring records by id and shows the effective web origin', () => {
    const detail: ForgeDetail = {
      ...GITHUB,
      repositories: [{ kind: 'repository', key: 'repo_1', label: 'blizzard' }],
      work_sources: [{ kind: 'work_source', key: 'wsrc_1', label: 'issues' }],
    };
    const vm = forgeRecordVm(detail)!;
    expect(vm.facts.find((fact) => fact.label === 'Web origin')?.value).toBe('https://github.com');
    expect(vm.links?.links).toEqual([
      { kind: 'repository', name: 'blizzard', route: ['/admin', 'repositories', 'repo_1'] },
      { kind: 'work source', name: 'issues', route: ['/admin', 'work-sources', 'wsrc_1'] },
    ]);
  });
});
