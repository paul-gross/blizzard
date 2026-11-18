import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { repositoryLinkView, stubHubClient } from 'fleet/testing';
import { injectProjectBranchService } from './project-branch.service';
import { projectBranchBaseline, projectBranchError } from './project-branch.domain';
import { projectBranchRefusal } from './project-branch.translate';

const before = { id: 'repo', projectId: 'project', revision: 7, override: 'old', draft: { baseBranch: 'old' } };
describe('project branch', () => {
  it('validates git ref rules', () => {
    for (const name of ['', ' ', 'a b', 'HEAD', '@', '-a', '/a', 'a/', 'a//b', 'a..b', 'a@{b', 'a.lock/b', '.a', 'a.', 'a~b', 'a\\b', 'a\u007fb', 'a\u00a0b']) expect(projectBranchError(name)).not.toBeNull();
    for (const name of ['main', 'feat/a.b', 'release/2.x']) expect(projectBranchError(name)).toBeNull();
    for (const char of ['\u0085', '\u001c', '\u001d', '\u001e', '\u001f']) expect(projectBranchError(`a${char}b`)).not.toBeNull();
  });
  it('maps hub field refusals and leaves conflicts at form level', () => {
    expect(projectBranchRefusal({ detail: [{ loc: ['body', 'base_branch'], msg: 'bad' }] })).toEqual({ ok: false, fields: { baseBranch: 'bad' }, form: null });
    expect(projectBranchRefusal({ detail: 'stale' })).toEqual({ ok: false, fields: {}, form: 'stale' });
  });
  it('captures identity, revision and a detached draft from the opening read', () => {
    const link = { project_id: 'p', repository_id: 'r', revision: 3, base_branch: null as string | null, name: 'R', slug: 'r', linked: true, position: null, effective_base_branch: 'main' };
    const baseline = projectBranchBaseline(link);
    link.project_id = 'other';
    link.revision = 4;
    link.base_branch = 'main';
    expect(baseline).toEqual({ id: 'r', projectId: 'p', revision: 3, override: null, draft: { baseBranch: '' } });
  });
  it('writes a changed override as a string, but does not implicitly inherit a blank', async () => {
    const stub = stubHubClient({ 'PATCH /api/projects/{project}/repository-links/{repository}': () => repositoryLinkView() });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())] });
    const service = TestBed.runInInjectionContext(injectProjectBranchService);
    const inherited = { ...before, override: null, draft: { baseBranch: '' } };
    expect((await service.save(inherited, { baseBranch: '' })).ok).toBe(false);
    expect(await service.inherit(inherited)).toEqual({ ok: true });
    expect(stub.forRoute('/api/projects/project/repository-links/repo', 'PATCH')).toHaveLength(0);
    expect(await service.save(inherited, { baseBranch: 'feature/new' })).toEqual({ ok: true });
    const call = stub.forRoute('/api/projects/project/repository-links/repo', 'PATCH')[0];
    expect(call.body).toEqual({ base_branch: 'feature/new' });
    expect(call.headers['if-match']).toBe('7');
    stub.restore();
  });
  it('guards blank and unchanged saves, and uses captured identity until invalidation settles', async () => {
    const stub = stubHubClient({ 'PATCH /api/projects/{project}/repository-links/{repository}': () => repositoryLinkView() });
    const client = new QueryClient();
    let settle!: () => void;
    vi.spyOn(client, 'invalidateQueries').mockReturnValue(new Promise<void>((resolve) => { settle = resolve; }));
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    const service = TestBed.runInInjectionContext(injectProjectBranchService);
    expect((await service.save(before, { baseBranch: '' })).ok).toBe(false);
    expect((await service.save(before, { baseBranch: 'old' })).ok).toBe(true);
    expect(stub.forRoute('/api/projects/project/repository-links/repo', 'PATCH')).toHaveLength(0);
    const saving = service.inherit(before);
    await vi.waitFor(() => expect(stub.forRoute('/api/projects/project/repository-links/repo', 'PATCH')).toHaveLength(1));
    const call = stub.forRoute('/api/projects/project/repository-links/repo', 'PATCH')[0];
    expect(call.body).toEqual({ base_branch: null });
    expect(call.headers['if-match']).toBe('7');
    let done = false;
    void saving.then(() => { done = true; });
    await Promise.resolve();
    expect(done).toBe(false);
    settle();
    expect(await saving).toEqual({ ok: true });
    stub.restore();
  });
});
