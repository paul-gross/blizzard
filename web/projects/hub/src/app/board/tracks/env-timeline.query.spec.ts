import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, type hubApi } from 'fleet';
import { HUB_PROJECT_FILTER } from 'fleet/shell';
import { type RequestClientStub, settle, stubHubClient } from 'fleet/testing';
import { LOOKBACK_MS, type TimelineRange } from './tracks-window.model';
import {
  injectHubEnvTimelineLiveQuery,
  injectHubEnvTimelineNowQuery,
  injectHubEnvTimelineRangeQuery,
} from './env-timeline.query';

const HOUR = 60 * 60_000;
const NOW = Date.UTC(2026, 0, 15, 12, 0, 0);

function block(chunkId: string, startedAt: string, endedAt: string | null): hubApi.EnvTimelineBlockView {
  return {
    chunk_id: chunkId,
    cost_estimated: false,
    cost_usd: 0,
    ended_at: endedAt,
    epoch: 1,
    escalation: null,
    escalation_open: false,
    executor: 'runner',
    exit_choice: null,
    failed: false,
    lease_id: null,
    node_id: 'nd_build',
    runner_id: 'rn_1',
    started_at: startedAt,
  };
}

function timeline(since: string, generatedAt: string, blocks: hubApi.EnvTimelineBlockView[]): hubApi.EnvTimelineView {
  return {
    chunks: {},
    generated_at: generatedAt,
    hosts: [
      {
        host_id: 'h_1',
        host_name: 'host',
        runner_names: [],
        workspaces: [{ workspace_id: 'ws_1', environments: [{ environment_id: 'env_1', blocks }] }],
      },
    ],
    nodes: {},
    runners: { rn_1: 'runner' },
    since,
    until: generatedAt,
  };
}

@Component({
  selector: 'app-test-env-timeline-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestEnvTimelineHost {
  readonly cover = signal(6 * HOUR);
  readonly range = signal<TimelineRange | null>(null);
  readonly live = injectHubEnvTimelineLiveQuery(() => this.cover());
  readonly now = injectHubEnvTimelineNowQuery();
  readonly ranged = injectHubEnvTimelineRangeQuery(() => this.range());
}

describe('env-timeline queries', () => {
  let stub: RequestClientStub;
  let clock: number;
  let served: hubApi.EnvTimelineView[];
  afterEach(() => stub?.restore());

  function mount(lens: string | null = null) {
    clock = NOW;
    served = [];
    stub = stubHubClient({
      'GET /api/env-timeline': () => served.shift() ?? timeline('', new Date(clock).toISOString(), []),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: FLEET_CLOCK, useValue: () => clock },
        { provide: HUB_PROJECT_FILTER, useValue: signal(lens).asReadonly() },
      ],
    });
    return TestBed.createComponent(TestEnvTimelineHost);
  }

  function reads(): URLSearchParams[] {
    return stub.forRoute('/api/env-timeline', 'GET').map((r) => new URLSearchParams(r.search));
  }

  it('reads the live window whole from the cover and an hour of slack back', async () => {
    const fixture = mount();
    await settle(fixture);

    const live = reads().find((q) => !q.has('latest'));
    expect(live?.get('since')).toBe(new Date(NOW - 7 * HOUR).toISOString());
    expect(live?.has('until')).toBe(false);
  });

  it('reads what is happening now over the widest lookback, latest steps only', async () => {
    const fixture = mount();
    await settle(fixture);

    const now = reads().find((q) => q.get('latest') === 'true');
    expect(now?.get('since')).toBe(new Date(NOW - LOOKBACK_MS).toISOString());
  });

  it('catches the live window up from two minutes before the last answer and merges', async () => {
    const fixture = mount();
    const generated = new Date(NOW - 60_000).toISOString();
    const first = timeline(new Date(NOW - 7 * HOUR).toISOString(), generated, [
      block('chk_old', new Date(NOW - 5 * HOUR).toISOString(), new Date(NOW - 4 * HOUR).toISOString()),
      block('chk_run', new Date(NOW - 30 * 60_000).toISOString(), null),
    ]);
    served = [first, first];
    await settle(fixture);
    const before = reads().length;

    const sinceCatchUp = new Date(Date.parse(generated) - 2 * 60_000).toISOString();
    served = [
      timeline(sinceCatchUp, new Date(NOW).toISOString(), [
        block('chk_run', new Date(NOW - 30 * 60_000).toISOString(), new Date(NOW - 10 * 60_000).toISOString()),
      ]),
    ];
    clock = NOW + 1000;
    await fixture.componentInstance.live.refetch();
    await settle(fixture);

    const catchUps = reads().slice(before).filter((q) => q.get('since') === sinceCatchUp && !q.has('latest'));
    expect(catchUps).toHaveLength(1);
    const segments = fixture.componentInstance.live
      .data()
      ?.hosts.flatMap((h) => h.workspaces.flatMap((w) => w.environments.flatMap((e) => e.segments)));
    expect(segments?.map((s) => s.chunk_id)).toEqual(['chk_old', 'chk_run']);
    expect(segments?.[1].ended_at).toBe(new Date(NOW - 10 * 60_000).toISOString());
  });

  it('sends the project lens when one is chosen', async () => {
    const fixture = mount('acme');
    await settle(fixture);

    expect(reads().length).toBeGreaterThan(0);
    for (const q of reads()) expect(q.get('project')).toBe('acme');
  });

  it('sends no project on All', async () => {
    const fixture = mount(null);
    await settle(fixture);

    for (const q of reads()) expect(q.has('project')).toBe(false);
  });

  it('reads no range until one is named, then reads its bounds', async () => {
    const fixture = mount();
    await settle(fixture);
    expect(reads().filter((q) => q.has('until'))).toHaveLength(0);

    const range = { since: '2026-01-01T00:00:00.000Z', until: '2026-01-02T00:00:00.000Z' };
    fixture.componentInstance.range.set(range);
    await settle(fixture);

    const ranged = reads().filter((q) => q.has('until'));
    expect(ranged).toHaveLength(1);
    expect(ranged[0].get('since')).toBe(range.since);
    expect(ranged[0].get('until')).toBe(range.until);
  });
});
