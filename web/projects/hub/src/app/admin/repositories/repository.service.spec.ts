import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { repositorySummary, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { injectRepositoryRecordQuery } from './repositories.query';
import { repository, repositoryDraft } from './repository.builders';
import { PARK_TIMEOUT_MESSAGE } from './repository.domain';
import { injectRepositoryService } from './repository.service';
import { toEditBaseline } from './repository.translate';

const SAVED = repositorySummary({ repository_id: 'repo_1', slug: 'blizzard' });
const PROBLEM = { instance: '/api/repositories', status: 409, title: 'Conflict' };
@Component({ selector: 'app-repository-service-host', template: '' })
class ServiceHost {
  readonly service = injectRepositoryService();
}

const BAD_HOURS = ['', '4.5', '-1', '0', '9007199254740992', '99999999999999999999'];

describe('injectRepositoryService', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  afterEach(() => {
    stub.restore();
    TestBed.resetTestingModule();
  });

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    return TestBed.runInInjectionContext(() => injectRepositoryService());
  }

  const posts = () => stub.forRoute('/api/repositories', 'POST');
  const patches = () => stub.forRoute('/api/repositories/repo_1', 'PATCH');

  describe('create', () => {
    it('sends the translated draft, Allow no CI false included', async () => {
      const service = configure({ 'POST /api/repositories': () => SAVED });
      expect(await service.create(repositoryDraft({ allowNoCi: false }))).toEqual({ ok: true });
      expect(posts()).toHaveLength(1);
      expect(posts()[0].body).toEqual({
        slug: 'blizzard',
        forge: 'frg_github',
        owner: 'paul-gross',
        repo: 'blizzard',
        base_branch: 'master',
        credential: 'cred_1',
        allow_no_ci: false,
        landing_policy: 'merge-on-checks',
      });
    });

    it('sends Allow no CI true', async () => {
      const service = configure({ 'POST /api/repositories': () => SAVED });
      await service.create(repositoryDraft({ allowNoCi: true }));
      expect(posts()[0].body).toMatchObject({ allow_no_ci: true });
    });

    it('sends null only for the explicit default', async () => {
      const service = configure({ 'POST /api/repositories': () => SAVED });
      await service.create(repositoryDraft({ landingPolicy: 'park', parkTimeoutDefault: true }));
      await service.create(repositoryDraft({ landingPolicy: 'park', parkTimeoutDefault: false, parkTimeoutHours: '12' }));
      expect(posts().map((request) => (request.body as { park_timeout_hours: unknown }).park_timeout_hours)).toEqual([null, 12]);
    });

    it.each(BAD_HOURS)('sends nothing for stated hours %j and marks the hours field', async (hours) => {
      const service = configure({ 'POST /api/repositories': () => SAVED });
      const outcome = await service.create(
        repositoryDraft({ landingPolicy: 'park', parkTimeoutDefault: false, parkTimeoutHours: hours }),
      );
      expect(outcome).toEqual({ ok: false, fields: { parkTimeoutHours: PARK_TIMEOUT_MESSAGE }, form: null });
      expect(posts()).toHaveLength(0);
    });

    it('lands a taken slug on slug and a taken owner/repo on repo', async () => {
      let type: 'slug' | 'coordinate' = 'slug';
      const service = configure({
        'POST /api/repositories': () =>
          type === 'slug'
            ? stubError(409, { ...PROBLEM, type: 'urn:blizzard:problem:repository-slug-taken' as const, detail: 'Slug taken.' })
            : stubError(409, {
                ...PROBLEM,
                type: 'urn:blizzard:problem:repository-coordinate-taken' as const,
                detail: 'Already registered.',
              }),
      });
      expect(await service.create(repositoryDraft())).toEqual({ ok: false, fields: { slug: 'Slug taken.' }, form: null });
      type = 'coordinate';
      expect(await service.create(repositoryDraft())).toEqual({ ok: false, fields: { repo: 'Already registered.' }, form: null });
    });

    it('reports pending while the create is in flight', async () => {
      let release!: () => void;
      const held = new Promise<void>((resolve) => (release = resolve));
      configure({ 'POST /api/repositories': async () => (await held, SAVED) });
      const fixture = TestBed.createComponent(ServiceHost);
      const { service } = fixture.componentInstance;
      await fixture.whenStable();
      expect(service.pending()).toBe(false);
      const saved = service.create(repositoryDraft());
      await vi.waitFor(() => expect(posts()).toHaveLength(1));
      fixture.detectChanges();
      expect(service.pending()).toBe(true);
      release();
      expect(await saved).toEqual({ ok: true });
      await vi.waitFor(() => {
        fixture.detectChanges();
        expect(service.pending()).toBe(false);
      });
    });
  });

  describe('edit', () => {
    it('sends the baseline revision as If-Match and only the changed field', async () => {
      const service = configure({ 'PATCH /api/repositories/{repository}': () => SAVED });
      const baseline = toEditBaseline(repository({ revision: 3, landingPolicy: 'park', parkTimeoutHours: 6 }));
      expect(await service.edit(baseline, { ...baseline.draft, parkTimeoutHours: '48' })).toEqual({ ok: true });
      expect(patches()).toHaveLength(1);
      expect(patches()[0].headers['if-match']).toBe('3');
      expect(patches()[0].body).toEqual({ park_timeout_hours: 48 });
    });

    it('sends Allow no CI turned off as false', async () => {
      const service = configure({ 'PATCH /api/repositories/{repository}': () => SAVED });
      const baseline = toEditBaseline(repository({ allowNoCi: true }));
      await service.edit(baseline, { ...baseline.draft, allowNoCi: false });
      expect(patches()[0].body).toEqual({ allow_no_ci: false });
    });

    it('sends null only for a newly chosen default', async () => {
      const service = configure({ 'PATCH /api/repositories/{repository}': () => SAVED });
      const baseline = toEditBaseline(repository({ landingPolicy: 'park', parkTimeoutHours: 6 }));
      await service.edit(baseline, { ...baseline.draft, parkTimeoutDefault: true });
      expect(patches()[0].body).toEqual({ park_timeout_hours: null });
    });

    it.each(BAD_HOURS)('sends nothing for stated hours %j and marks the hours field', async (hours) => {
      const service = configure({ 'PATCH /api/repositories/{repository}': () => SAVED });
      const baseline = toEditBaseline(repository({ landingPolicy: 'park', parkTimeoutHours: 6 }));
      const outcome = await service.edit(baseline, { ...baseline.draft, parkTimeoutHours: hours });
      expect(outcome).toEqual({ ok: false, fields: { parkTimeoutHours: PARK_TIMEOUT_MESSAGE }, form: null });
      expect(patches()).toHaveLength(0);
    });

    it('keeps the baseline revision as If-Match after the read refreshes to a newer one', async () => {
      let revision = 3;
      const service = configure({
        'GET /api/repositories/{repository}': () => repositorySummary({ repository_id: 'repo_1', revision }),
        'PATCH /api/repositories/{repository}': () =>
          stubError(409, { ...PROBLEM, type: 'urn:blizzard:problem:revision-conflict' as const, detail: 'The repository changed.' }),
      });
      const record = TestBed.runInInjectionContext(() => injectRepositoryRecordQuery(() => 'repo_1'));
      await vi.waitFor(() => expect(record.data()?.revision).toBe(3));
      const baseline = toEditBaseline(record.data()!);

      revision = 9;
      await queryClient.refetchQueries();
      await vi.waitFor(() => expect(record.data()?.revision).toBe(9));

      const outcome = await service.edit(baseline, { ...baseline.draft, name: 'Core' });
      expect(patches()[0].headers['if-match']).toBe('3');
      expect(outcome).toEqual({ ok: false, fields: {}, form: 'The repository changed.' });
      expect(baseline.revision).toBe(3);
      expect(patches()).toHaveLength(1);
    });

    it('resolves a save only once the invalidation has refetched', async () => {
      const service = configure({ 'PATCH /api/repositories/{repository}': () => SAVED });
      let release!: () => void;
      const refetched = new Promise<void>((resolve) => (release = resolve));
      const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
      const baseline = toEditBaseline(repository());

      let done = false;
      const saved = service.edit(baseline, { ...baseline.draft, name: 'Core' }).then(() => (done = true));
      await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(done).toBe(false);
      release();
      await saved;
      expect(done).toBe(true);
    });
  });
});
