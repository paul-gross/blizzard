import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { gardenFindingView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';

import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { emptyTriageDraft } from './finding-triage.domain';
import { injectFindingTriageService } from './finding-triage.service';

const FINDING = gardenFindingView({ finding_id: 'fnd_1' });

const ROUTES = {
  'POST /api/findings/resolve': () => [FINDING],
  'POST /api/findings/confirm-gone': () => [FINDING],
  'POST /api/findings/wont-fix': () => [FINDING],
  'POST /api/findings/not-a-finding': () => [FINDING],
  'POST /api/findings/supersede': () => [FINDING],
  'POST /api/findings/reopen': () => [FINDING],
};

const CASES: readonly { verb: FindingTriageVerb; route: string; extra?: Record<string, unknown> }[] = [
  { verb: 'resolve', route: '/api/findings/resolve' },
  { verb: 'confirm-gone', route: '/api/findings/confirm-gone' },
  { verb: 'wont-fix', route: '/api/findings/wont-fix' },
  { verb: 'not-a-finding', route: '/api/findings/not-a-finding' },
  { verb: 'supersede', route: '/api/findings/supersede', extra: { superseded_by: 'fnd_9' } },
  { verb: 'reopen', route: '/api/findings/reopen' },
];

@Component({ selector: 'app-triage-service-host', template: '' })
class Host {
  readonly service = injectFindingTriageService();
}

function setup(routes: Parameters<typeof stubHubClient>[0] = {}) {
  const stub = stubHubClient({ ...ROUTES, ...routes });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
  });
  const fixture = TestBed.createComponent(Host);
  return { stub, queryClient, fixture, service: fixture.componentInstance.service };
}

describe('injectFindingTriageService', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  for (const { verb, route, extra } of CASES) {
    it(`${verb} fires exactly its request once with the ids and translated body`, async () => {
      const s = setup();
      stub = s.stub;
      const outcome = await s.service.triage(['fnd_1', 'fnd_2'], {
        verb,
        note: ' a note ',
        supersededBy: ' fnd_9 ',
      });
      expect(outcome).toEqual({ ok: true });
      const calls = stub.forRoute(route, 'POST');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual({ finding_ids: ['fnd_1', 'fnd_2'], note: 'a note', ...extra });
    });
  }

  it('resolves only after the finding caches are invalidated', async () => {
    const s = setup();
    stub = s.stub;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.spyOn(s.queryClient, 'invalidateQueries').mockImplementation(() => gate);

    let settled = false;
    const pending = s.service.triage(['fnd_1'], { ...emptyTriageDraft('resolve'), note: 'n' }).then((o) => {
      settled = true;
      return o;
    });
    await vi.waitFor(() => expect(stub.forRoute('/api/findings/resolve', 'POST')).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(settled).toBe(false);

    release();
    expect(await pending).toEqual({ ok: true });
  });

  it('answers a refusal as a SaveOutcome instead of throwing it', async () => {
    const s = setup({
      'POST /api/findings/resolve': () =>
        stubError(409, { type: 'urn:blizzard:problem:finding-transition-refused', detail: 'already resolved' }),
    });
    stub = s.stub;
    const outcome = await s.service.triage(['fnd_1'], { ...emptyTriageDraft('resolve'), note: 'n' });
    expect(outcome).toEqual({ ok: false, fields: {}, form: 'already resolved' });
  });
});
