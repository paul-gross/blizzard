import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import {
  TranscriptFetchError,
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
  readonly final = signal(false);
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
});
