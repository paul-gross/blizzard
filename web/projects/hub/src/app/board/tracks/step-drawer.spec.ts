import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { vi } from 'vitest';

import { FLEET_CLOCK, formatHeldFor, type hubApi } from 'fleet';
import { type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';
import type { EnvTimelineStepView } from './step.query';
import { StepDrawer } from './step-drawer';

const BUILD: hubApi.EnvTimelinePlacedStepView = {
  environment_id: 'env_a',
  host_id: 'host_01',
  host_name: 'xps',
  workspace_id: 'ws_1',
  segment: {
    chunk_id: 'ch_01',
    cost_estimated: false,
    cost_usd: 0,
    ended_at: '2026-07-13T10:30:00Z',
    epoch: 1,
    escalation: null,
    escalation_open: false,
    executor: 'runner',
    exit_choice: 'pass',
    failed: false,
    graph_id: 'gr_1',
    graph_name: 'delivery',
    lease_id: 'lease_01',
    node_id: 'nd_build',
    node_name: 'build',
    project_id: null,
    runner_id: 'rn_01',
    runner_name: 'runner-one',
    started_at: '2026-07-13T10:00:00Z',
    title: null,
  },
};

const VIEW: EnvTimelineStepView = {
  artifact_keys: [],
  escalations: [],
  generated_at: '2026-07-13T12:00:00Z',
  invocations: [],
  spend: null,
  journey: [BUILD],
  step: BUILD,
};

describe('StepDrawer', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  let browser = 0;
  afterEach(() => vi.useRealTimers());

  async function mount(url: string, route: Parameters<typeof stubHubClient>[0]['GET /api/env-timeline/step'] = () => VIEW) {
    stub = stubHubClient({ 'GET /api/env-timeline/step': route });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board', component: StepDrawer }]),
        { provide: FLEET_CLOCK, useValue: () => browser },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.routeNativeElement as HTMLElement };
  }

  const reads = () => stub.forRoute('/api/env-timeline/step', 'GET');

  it('is closed and reads nothing while no chunk is named', async () => {
    const { el } = await mount('/board');

    expect(el.classList).toContain('closed');
    expect(reads()).toHaveLength(0);
  });

  it('reads the step by its session when ?step names one, and renders it', async () => {
    const { el } = await mount('/board?chunk=ch_01&step=lease_01');

    expect(el.classList).not.toContain('closed');
    expect(reads()[0].search).toContain('chunk_id=ch_01');
    expect(reads()[0].search).toContain('lease_id=lease_01');
    expect(reads()[0].search).not.toContain('started_at');
    expect(el.querySelector('[data-testid="step-drawer-lease"]')?.textContent?.trim()).toBe('lease_01');
  });

  it('reads the step by the instant it began when ?at names one', async () => {
    await mount('/board?chunk=ch_01&at=2026-07-13T10:00:00Z');

    expect(reads()[0].search).toContain('started_at=2026-07-13T10%3A00%3A00Z');
    expect(reads()[0].search).not.toContain('lease_id');
  });

  it('reads the chunk newest step when ?step is absent', async () => {
    await mount('/board?chunk=ch_01');

    expect(reads()[0].search).toContain('chunk_id=ch_01');
    expect(reads()[0].search).not.toContain('lease_id');
    expect(reads()[0].search).not.toContain('started_at');
  });

  it('rests on its empty state, with no retry, for a chunk with no node step yet', async () => {
    const { el } = await mount('/board?chunk=ch_01&layout=tracks', () => stubError(404, { detail: 'no such node step of ch_01' }));

    expect(el.classList).not.toContain('closed');
    expect(el.querySelector('[data-testid="step-drawer-empty"]')?.textContent).toContain('NO NODE STEP YET');
    expect(el.querySelector('[data-testid="step-drawer-error"]')).toBeNull();
    expect(reads()).toHaveLength(1);
  });

  it('closes from its empty state, keeping the other query params', async () => {
    const { harness, el } = await mount('/board?chunk=ch_01&layout=tracks', () => stubError(404, { detail: 'no such node step of ch_01' }));
    const router = TestBed.inject(Router);

    el.querySelector<HTMLElement>('[data-testid="step-drawer-close"]')!.click();
    await settle(harness.fixture);

    expect(router.parseUrl(router.url).queryParams).toEqual({ layout: 'tracks' });
  });

  it('holds a running step on to the ticking hub clock, not the instant the read answered', async () => {
    // Only the shared clock's interval is faked; the browser clock is the stub, an hour behind the hub's.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const answered = Date.parse(VIEW.generated_at);
    browser = answered - 3_600_000;
    const running = { ...BUILD, segment: { ...BUILD.segment, ended_at: null } };
    const { harness, el } = await mount('/board?chunk=ch_01', () => ({ ...VIEW, journey: [running], step: running }));
    const held = () => [...el.querySelectorAll('dd')].map((d) => d.textContent?.trim()).find((t) => t?.endsWith('so far'));
    const started = Date.parse(BUILD.segment.started_at);
    expect(held()).toBe(`${formatHeldFor(answered - started)} so far`);

    browser += 5 * 60_000;
    vi.advanceTimersByTime(1_000);
    await settle(harness.fixture);

    expect(held()).toBe(`${formatHeldFor(answered + 5 * 60_000 - started)} so far`);
  });

  it('navigates to the picked step, keeping the other query params', async () => {
    const { harness, el } = await mount('/board?chunk=ch_01&step=lease_01&layout=tracks');
    const router = TestBed.inject(Router);

    el.querySelector<HTMLElement>('.sd-blk')!.click();
    await settle(harness.fixture);

    const params = router.parseUrl(router.url).queryParams;
    expect(params).toEqual({ chunk: 'ch_01', step: 'lease_01', layout: 'tracks' });
  });

  it('names a picked step no session ran by ?at alone, dropping ?step', async () => {
    const hubSide = { ...BUILD, segment: { ...BUILD.segment, lease_id: null, epoch: null } };
    const { harness, el } = await mount('/board?chunk=ch_01&step=lease_01&layout=tracks', () => ({ ...VIEW, journey: [hubSide, BUILD] }));
    const router = TestBed.inject(Router);

    el.querySelector<HTMLElement>('.sd-blk')!.click();
    await settle(harness.fixture);

    expect(router.parseUrl(router.url).queryParams).toEqual({ chunk: 'ch_01', at: '2026-07-13T10:00:00Z', layout: 'tracks' });
  });

  it('clears chunk and step on dismiss, keeping the other query params', async () => {
    const { harness, el } = await mount('/board?chunk=ch_01&step=lease_01&layout=tracks');
    const router = TestBed.inject(Router);

    el.querySelector<HTMLElement>('[data-testid="step-drawer-close"]')!.click();
    await settle(harness.fixture);

    expect(router.parseUrl(router.url).queryParams).toEqual({ layout: 'tracks' });
    expect(el.classList).toContain('closed');
  });
});
