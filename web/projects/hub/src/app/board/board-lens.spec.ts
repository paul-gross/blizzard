import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { injectHubBoardChunksQuery } from '../core/chunks.query';
import { provideProjectLens } from '../core/project-lens';
import { injectHubActivityQuery } from './activity/activity.query';
import { injectHubChunkCountsQuery } from './chunks/chunk-counts.query';
import { injectHubFleetSpendQuery } from './fleet-spend/fleet-spend.query';
import { injectHubDecisionsQuery } from './gates/gates.query';
import { injectHubBacklogQuery, injectHubQueueQuery } from './queue/queue.query';
import { injectHubQuestionsQuery } from './questions/questions.query';

@Component({ selector: 'app-test-lens-view', template: '' })
class TestView {}

/** Every read the board makes, mounted together. */
@Component({
  selector: 'app-test-board-lens-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestBoardReads {
  readonly chunks = injectHubBoardChunksQuery();
  readonly counts = injectHubChunkCountsQuery();
  readonly queue = injectHubQueueQuery();
  readonly backlog = injectHubBacklogQuery(() => true);
  readonly questions = injectHubQuestionsQuery();
  readonly decisions = injectHubDecisionsQuery();
  readonly activity = injectHubActivityQuery();
  readonly spend = injectHubFleetSpendQuery(() => '2026-10-10T00:00:00Z');
}

/** The routes the board reads, each answered with an empty page but for the rows the filter tests use. */
function answer(_method: string, path: string): unknown {
  switch (path) {
    case '/api/chunks':
      return { chunks: [{ chunk_id: 'ch_alpha', status: 'needs_human' }], next_cursor: null };
    case '/api/chunk-counts':
      return { counts: {} };
    case '/api/queue':
    case '/api/backlog':
      return { entries: [], next_cursor: null };
    case '/api/questions':
      return [
        { question_id: 'q_alpha', chunk_id: 'ch_alpha' },
        { question_id: 'q_beta', chunk_id: 'ch_beta' },
      ];
    case '/api/decisions':
      return {
        decisions: [
          { decision_id: 'd_alpha', chunk_id: 'ch_alpha' },
          { decision_id: 'd_beta', chunk_id: 'ch_beta' },
        ],
      };
    case '/api/activity':
      return { activity: [] };
    default:
      return {};
  }
}

const LENSED_ROUTES = ['/api/chunks', '/api/chunk-counts', '/api/queue', '/api/backlog', '/api/activity', '/api/spend'];

describe('the board reads under the project lens', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, answer);
    await TestBed.configureTestingModule({
      imports: [TestBoardReads],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
        provideProjectLens(),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  async function mountAt(url: string) {
    await TestBed.inject(Router).navigateByUrl(url);
    const fixture = TestBed.createComponent(TestBoardReads);
    await settle(fixture);
    return fixture;
  }

  it('sends the lensed project on every chunk, count, queue, backlog, activity and spend read', async () => {
    await mountAt('/p/alpha/board');

    for (const route of LENSED_ROUTES) {
      const requests = stub.forRoute(route, 'GET');
      expect(requests.length, `${route} was not read`).toBeGreaterThan(0);
      for (const request of requests) {
        expect(new URLSearchParams(request.search).get('project'), `${route} lost the lens`).toBe('alpha');
      }
    }
  });

  it('reads the whole tenant on All, naming no project', async () => {
    await mountAt('/board');

    for (const route of LENSED_ROUTES) {
      const requests = stub.forRoute(route, 'GET');
      expect(requests.length, `${route} was not read`).toBeGreaterThan(0);
      for (const request of requests) {
        expect(new URLSearchParams(request.search).has('project'), `${route} named a project on All`).toBe(false);
      }
    }
  });

  it('keeps only the questions and decisions on the lensed project chunks', async () => {
    const fixture = await mountAt('/p/alpha/board');
    const reads = fixture.componentInstance;

    expect(reads.questions.data()?.map((q) => q.question_id)).toEqual(['q_alpha']);
    expect(reads.decisions.data()?.map((d) => d.decision_id)).toEqual(['d_alpha']);
  });

  it('keeps every question and decision on All', async () => {
    const fixture = await mountAt('/board');
    const reads = fixture.componentInstance;

    expect(reads.questions.data()?.map((q) => q.question_id)).toEqual(['q_alpha', 'q_beta']);
    expect(reads.decisions.data()?.map((d) => d.decision_id)).toEqual(['d_alpha', 'd_beta']);
  });
});
