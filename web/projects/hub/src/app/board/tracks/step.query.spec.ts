import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubEnvTimelineLiveKey, type hubApi } from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';
import { type StepRef } from './step.model';
import { injectHubEnvTimelineStepQuery, stepQueryOptions, stepRunning } from './step.query';

function stepView(chunkId: string, startedAt: string, endedAt: string | null = null): hubApi.EnvTimelineStepView {
  return {
    artifact_keys: [],
    escalations: [],
    generated_at: '2026-01-15T12:00:00Z',
    invocations: [],
    spend: null,
    journey: [],
    step: {
      environment_id: 'env_1',
      host_id: null,
      host_name: null,
      workspace_id: 'ws_1',
      segment: {
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
        graph_id: null,
        graph_name: null,
        lease_id: null,
        node_id: 'nd_build',
        node_name: 'build',
        project_id: null,
        runner_id: 'rn_1',
        runner_name: 'runner',
        started_at: startedAt,
        title: null,
      },
    },
  };
}

@Component({
  selector: 'app-test-step-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestStepQueryHost {
  readonly ref = signal<StepRef | null>(null);
  readonly query = injectHubEnvTimelineStepQuery(() => this.ref());
}

/** Pump a fixture whose read is held open — `settle` would wait on it forever. */
async function pending(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 5; i += 1) {
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

describe('step queries', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  let client: QueryClient;

  function mount(route?: Parameters<typeof stubHubClient>[0]['GET /api/env-timeline/step'], retry: number | false = false) {
    stub = stubHubClient({
      'GET /api/env-timeline/step':
        route ??
        ((request) => {
          const q = new URLSearchParams(request.search);
          return stepView(q.get('chunk_id') ?? '', q.get('started_at') ?? '2026-01-15T10:00:00Z');
        }),
    });
    client = new QueryClient({ defaultOptions: { queries: { retry, retryDelay: 0 } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    return TestBed.createComponent(TestStepQueryHost);
  }

  function lastQuery(): URLSearchParams {
    const calls = stub.forRoute('/api/env-timeline/step', 'GET');
    return new URLSearchParams(calls[calls.length - 1].search);
  }

  it('reads nothing while no step is named', async () => {
    const fixture = mount();
    await settle(fixture);

    expect(stub.requests).toHaveLength(0);
    expect(fixture.componentInstance.query.data()).toBeUndefined();
  });

  it('names a step by its session as lease_id alone', async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: 'lease_01ABC', at: null });
    await settle(fixture);

    expect(lastQuery().get('chunk_id')).toBe('chk_1');
    expect(lastQuery().get('lease_id')).toBe('lease_01ABC');
    expect(lastQuery().has('started_at')).toBe(false);
  });

  it('names a step by the instant it began as started_at alone', async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: '2026-01-15T09:00:00Z' });
    await settle(fixture);

    expect(lastQuery().get('started_at')).toBe('2026-01-15T09:00:00Z');
    expect(lastQuery().has('lease_id')).toBe(false);
  });

  it("names neither for the chunk's newest step", async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: null });
    await settle(fixture);

    expect(lastQuery().get('chunk_id')).toBe('chk_1');
    expect(lastQuery().has('lease_id')).toBe(false);
    expect(lastQuery().has('started_at')).toBe(false);
  });

  it('keeps the previous answer while switching to another step of the same chunk', async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: '2026-01-15T09:00:00Z' });
    await settle(fixture);
    expect(fixture.componentInstance.query.isPlaceholderData()).toBe(false);

    stub.restore();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    stub = stubHubClient({
      'GET /api/env-timeline/step': async (request) => {
        await gate;
        return stepView('chk_1', new URLSearchParams(request.search).get('started_at') ?? '');
      },
    });
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: '2026-01-15T10:00:00Z' });
    await pending(fixture);

    expect(fixture.componentInstance.query.isPlaceholderData()).toBe(true);
    expect(fixture.componentInstance.query.data()?.step.segment.started_at).toBe('2026-01-15T09:00:00Z');
    release();
    await settle(fixture);
    expect(fixture.componentInstance.query.isPlaceholderData()).toBe(false);
    expect(fixture.componentInstance.query.data()?.step.segment.started_at).toBe('2026-01-15T10:00:00Z');
  });

  it('shows no previous answer across chunks', async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: '2026-01-15T09:00:00Z' });
    await settle(fixture);

    stub.restore();
    const gate = new Promise<void>(() => undefined);
    stub = stubHubClient({
      'GET /api/env-timeline/step': async () => {
        await gate;
        return stepView('chk_2', '');
      },
    });
    fixture.componentInstance.ref.set({ chunkId: 'chk_2', session: null, at: '2026-01-15T09:00:00Z' });
    await pending(fixture);

    expect(fixture.componentInstance.query.isPlaceholderData()).toBe(false);
    expect(fixture.componentInstance.query.data()).toBeUndefined();
  });

  it('keys the shared read under the live root, by chunk and step name', () => {
    const key = stepQueryOptions({ chunkId: 'chk_1', session: 'lease_1', at: null }).queryKey;
    expect(key.slice(0, hubEnvTimelineLiveKey.length)).toEqual([...hubEnvTimelineLiveKey]);
    expect(key).toEqual(expect.arrayContaining(['step', 'chk_1', 'lease_1', null]));
    expect(stepQueryOptions({ chunkId: 'chk_1', session: null, at: '2026-01-15T09:00:00Z' }).queryKey).toEqual(
      expect.arrayContaining(['step', 'chk_1', null, '2026-01-15T09:00:00Z']),
    );
    expect(stepQueryOptions(null).queryKey).toEqual(expect.arrayContaining(['step', null, null, null]));
  });

  it('re-reads the step on screen when the live root is invalidated', async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: null });
    await settle(fixture);
    expect(stub.forRoute('/api/env-timeline/step', 'GET')).toHaveLength(1);

    await client.invalidateQueries({ queryKey: hubEnvTimelineLiveKey });
    await settle(fixture);

    expect(stub.forRoute('/api/env-timeline/step', 'GET')).toHaveLength(2);
  });

  it('polls a running step on the backstop, and stops once it has ended', async () => {
    let endedAt: string | null = null;
    const fixture = mount(() => stepView('chk_1', '2026-01-15T10:00:00Z', endedAt));
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: null });
    await settle(fixture);
    const interval = () => {
      const query = client.getQueryCache().find({ queryKey: stepQueryOptions({ chunkId: 'chk_1', session: null, at: null }).queryKey })!;
      const option = query.observers[0].options.refetchInterval;
      return typeof option === 'function' ? option(query as never) : option;
    };
    expect(interval()).toBe(LIVE_COVERED_POLL_BACKSTOP_MS);

    endedAt = '2026-01-15T10:30:00Z';
    await client.refetchQueries({ queryKey: hubEnvTimelineLiveKey });
    await settle(fixture);

    expect(interval()).toBe(false);
  });

  it('answers a chunk with no node step yet as null, at once and with no retry', async () => {
    const fixture = mount(() => stubError(404, { detail: 'no such node step of chk_1' }), 3);
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: null });
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(false);
    expect(fixture.componentInstance.query.data()).toBeNull();
    expect(stub.forRoute('/api/env-timeline/step', 'GET')).toHaveLength(1);
  });

  it('still fails on any other refusal', async () => {
    const fixture = mount(() => stubError(500, { detail: 'boom' }));
    fixture.componentInstance.ref.set({ chunkId: 'chk_1', session: null, at: null });
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
  });
});

describe('stepRunning', () => {
  it('is true only for a read step still running', () => {
    expect(stepRunning(stepView('chk_1', '2026-01-15T10:00:00Z'))).toBe(true);
    expect(stepRunning(stepView('chk_1', '2026-01-15T10:00:00Z', '2026-01-15T10:30:00Z'))).toBe(false);
    expect(stepRunning(null)).toBe(false);
    expect(stepRunning(undefined)).toBe(false);
  });
});
