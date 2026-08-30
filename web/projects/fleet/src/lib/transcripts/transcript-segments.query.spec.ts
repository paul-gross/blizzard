import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { Client } from '../api/hub/client';
import { client as hubClient } from '../api/hub/client.gen';
import { client as runnerClient } from '../api/runner/client.gen';
import type { TranscriptPlane } from '../query-keys';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import {
  TranscriptFetchError,
  injectChunkTranscriptSegmentQuery,
  injectChunkTranscriptsQuery,
  injectHubChunkTranscriptSegmentQuery,
  injectHubChunkTranscriptsQuery,
  shouldRetryTranscriptFetch,
} from './transcript-segments.query';

describe('shouldRetryTranscriptFetch', () => {
  it('never retries a 403 — no permission is not a transient failure', () => {
    expect(shouldRetryTranscriptFetch(0, new TranscriptFetchError(403))).toBe(false);
  });

  it('never retries a 404 — an unknown chunk or segment is not a transient failure', () => {
    expect(shouldRetryTranscriptFetch(0, new TranscriptFetchError(404))).toBe(false);
  });

  it('retries a non-403/404 TranscriptFetchError up to the default cap of 3 attempts', () => {
    expect(shouldRetryTranscriptFetch(0, new TranscriptFetchError(500))).toBe(true);
    expect(shouldRetryTranscriptFetch(3, new TranscriptFetchError(500))).toBe(false);
  });
});

@Component({
  selector: 'fleet-test-transcripts-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestTranscriptsQueryHost {
  readonly chunkId = signal<string | null>('ch_1');
  readonly query = injectHubChunkTranscriptsQuery(() => this.chunkId());
}

@Component({
  selector: 'fleet-test-transcript-segment-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestTranscriptSegmentQueryHost {
  readonly chunkId = signal<string | null>('ch_1');
  readonly segmentId = signal<string | null>(null);
  readonly final = signal<boolean | null>(false);
  readonly query = injectHubChunkTranscriptSegmentQuery(
    () => this.chunkId(),
    () => this.segmentId(),
    () => this.final(),
  );
}

describe('injectHubChunkTranscriptsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('renders a 403 as a TranscriptFetchError, not a generic network error', async () => {
    stub = stubRequestClient(hubClient, () => stubError(403, { detail: 'forbidden' }));
    TestBed.configureTestingModule({
      imports: [TestTranscriptsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestTranscriptsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
    expect((fixture.componentInstance.query.error() as TranscriptFetchError).status).toBe(403);
    expect(stub.forRoute('/api/chunks/ch_1/transcripts', 'GET')).toHaveLength(1);
  });
});

describe('injectHubChunkTranscriptSegmentQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('fires no request until a segment id is set — the lazy per-segment read (D8)', async () => {
    stub = stubRequestClient(hubClient, () => ({ segment_id: 's1', final: true, truncated: false, turns: [] }));
    TestBed.configureTestingModule({
      imports: [TestTranscriptSegmentQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestTranscriptSegmentQueryHost);
    await settle(fixture);

    expect(stub.requests).toHaveLength(0);

    fixture.componentInstance.segmentId.set('seg-1');
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_1/transcripts/seg-1', 'GET')).toHaveLength(1);
    expect(fixture.componentInstance.query.data()?.segment_id).toBe('s1');
  });

  it('fires no request while finality is unknown, then exactly one once it resolves', async () => {
    stub = stubRequestClient(hubClient, () => ({ segment_id: 's1', final: true, truncated: false, turns: [] }));
    TestBed.configureTestingModule({
      imports: [TestTranscriptSegmentQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestTranscriptSegmentQueryHost);
    fixture.componentInstance.final.set(null);
    fixture.componentInstance.segmentId.set('seg-1');
    await settle(fixture);

    // `null` is "the index has not named this segment's finality yet" — a segment id alone
    // must not be enough to fetch, or the read goes out against a guessed key placement.
    expect(stub.requests).toHaveLength(0);

    fixture.componentInstance.final.set(true);
    await settle(fixture);

    // Exactly one: resolving finality moves the key, but only from a placement that never
    // ran, so the segment's content is fetched once rather than once per placement.
    expect(stub.forRoute('/api/chunks/ch_1/transcripts/seg-1', 'GET')).toHaveLength(1);
    expect(fixture.componentInstance.query.data()?.segment_id).toBe('s1');
  });
});

/** A real call site passes `client`/`plane` as closed-over constants, exactly like
 * {@link injectHubChunkTranscriptsQuery} does internally — never as reactive Angular
 * inputs, since which plane a component reads from never changes over its lifetime. One
 * host class per plane mirrors that: each closes over its own plane's client the same way
 * a hub-app or runner-app call site would (D5). */
function definePlaneTranscriptsQueryHost(client: Client, plane: TranscriptPlane) {
  @Component({
    selector: 'fleet-test-plane-transcripts-query-host',
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: '',
  })
  class TestPlaneTranscriptsQueryHost {
    readonly chunkId = signal<string | null>('ch_1');
    readonly query = injectChunkTranscriptsQuery(
      () => client,
      () => plane,
      () => this.chunkId(),
    );
  }
  return TestPlaneTranscriptsQueryHost;
}

describe('injectChunkTranscriptsQuery — plane-generic (D5)', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it.each([
    ['hub', hubClient] as const,
    ['runner', runnerClient] as const,
  ])('resolves against the %s plane’s own stubbed client', async (plane, client) => {
    stub = stubRequestClient(client, () => ({ chunk_id: 'ch_1', segments: [] }));
    const HostComponent = definePlaneTranscriptsQueryHost(client, plane);
    TestBed.configureTestingModule({
      imports: [HostComponent],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(HostComponent);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.chunk_id).toBe('ch_1');
    expect(stub.forRoute('/api/chunks/ch_1/transcripts', 'GET')).toHaveLength(1);
  });
});

describe('injectChunkTranscriptSegmentQuery — plane-generic (D5)', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it.each([
    ['hub', hubClient] as const,
    ['runner', runnerClient] as const,
  ])('resolves against the %s plane’s own stubbed client', async (plane, client) => {
    stub = stubRequestClient(client, () => ({ segment_id: 's1', final: true, truncated: false, turns: [] }));

    @Component({
      selector: 'fleet-test-plane-transcript-segment-query-host',
      changeDetection: ChangeDetectionStrategy.OnPush,
      template: '',
    })
    class TestPlaneTranscriptSegmentQueryHost {
      readonly query = injectChunkTranscriptSegmentQuery(
        () => client,
        () => plane,
        () => 'ch_1',
        () => 'seg-1',
        () => true,
      );
    }
    TestBed.configureTestingModule({
      imports: [TestPlaneTranscriptSegmentQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestPlaneTranscriptSegmentQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.segment_id).toBe('s1');
    expect(stub.forRoute('/api/chunks/ch_1/transcripts/seg-1', 'GET')).toHaveLength(1);
  });
});
