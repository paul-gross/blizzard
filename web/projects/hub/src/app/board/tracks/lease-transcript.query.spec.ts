import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubEnvTimelineKey } from 'fleet';
import { type RequestClientStub, settle, stubError, stubHubClient, turnSegmentView } from 'fleet/testing';
import { type LeaseTranscriptRef, injectLeaseTranscriptQuery } from './lease-transcript.query';

@Component({
  selector: 'app-test-lease-transcript-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestLeaseTranscriptHost {
  readonly ref = signal<LeaseTranscriptRef | null>(null);
  readonly query = injectLeaseTranscriptQuery(() => this.ref());
}

const REF: LeaseTranscriptRef = { chunkId: 'chk_1', nodeId: 'nd_build', epoch: 2, live: false };

type Route = Parameters<typeof stubHubClient>[0]['GET /api/env-timeline/step/transcript'];

describe('injectLeaseTranscriptQuery', () => {
  let stub: RequestClientStub;
  let client: QueryClient;
  afterEach(() => stub?.restore());

  const transcript = (truncated = false) => ({
    chunk_id: 'chk_1',
    node_id: 'nd_build',
    epoch: 2,
    truncated,
    turns: [turnSegmentView({ index: 0, text: 'first' }), turnSegmentView({ index: 1, text: 'second' })],
  });

  function mount(route: Route = () => transcript(), retry: number | false = false) {
    stub = stubHubClient({ 'GET /api/env-timeline/step/transcript': route });
    client = new QueryClient({ defaultOptions: { queries: { retry, retryDelay: 0 } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    return TestBed.createComponent(TestLeaseTranscriptHost);
  }

  const reads = () => stub.forRoute('/api/env-timeline/step/transcript', 'GET');

  it('reads nothing while no session is named', async () => {
    const fixture = mount();
    await settle(fixture);

    expect(stub.requests).toHaveLength(0);
  });

  it("reads the step's transcript by its chunk, node and epoch, turns in the hub's order", async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set(REF);
    await settle(fixture);

    const q = new URLSearchParams(reads()[0].search);
    expect([q.get('chunk_id'), q.get('node_id'), q.get('epoch')]).toEqual(['chk_1', 'nd_build', '2']);
    expect(fixture.componentInstance.query.data()?.turns.map((t) => t.text)).toEqual(['first', 'second']);
    expect(fixture.componentInstance.query.data()?.truncated).toBe(false);
  });

  it('carries the truncation the hub reports', async () => {
    const fixture = mount(() => transcript(true));
    fixture.componentInstance.ref.set(REF);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.truncated).toBe(true);
  });

  it('answers a caller who may not read transcripts as null, at once and with no retry', async () => {
    const fixture = mount(() => stubError(403, { detail: 'transcript:read required' }), 3);
    fixture.componentInstance.ref.set({ ...REF, live: true });
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(false);
    expect(fixture.componentInstance.query.data()).toBeNull();
    expect(reads()).toHaveLength(1);
    expect(pollOf(fixture.componentInstance.ref()!)).toBe(false);
  });

  it('fails on any other refusal', async () => {
    const fixture = mount(() => stubError(404, { detail: 'unknown chunk' }));
    fixture.componentInstance.ref.set(REF);
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
  });

  it("polls a live step's transcript, and never a finished one's", async () => {
    const fixture = mount();
    fixture.componentInstance.ref.set({ ...REF, live: true });
    await settle(fixture);
    expect(pollOf(fixture.componentInstance.ref()!)).toBe(15_000);

    fixture.componentInstance.ref.set(REF);
    await settle(fixture);
    expect(pollOf(REF)).toBe(false);
  });

  function pollOf(ref: LeaseTranscriptRef): unknown {
    const query = client.getQueryCache().find({ queryKey: [...hubEnvTimelineKey, 'transcript', ref.chunkId, ref.nodeId, ref.epoch] })!;
    const option = query.observers[0].options.refetchInterval;
    return typeof option === 'function' ? option(query as never) : option;
  }
});
