import {
  ChangeDetectionStrategy,
  Component,
  provideZonelessChangeDetection,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import {
  QueryClient,
  provideTanStackQuery,
} from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import {
  type RequestClientStub,
  settle,
  stubRequestClient,
} from 'fleet/testing';
import { injectHubBoardChunksQuery } from '../core/chunks.query';
import { provideProjectLens } from '../core/project-lens';
import { injectHubActivityQuery } from './activity/activity.query';
import { injectHubChunkCountsQuery } from './chunks/chunk-counts.query';
import { injectHubFleetSpendQuery } from './fleet-spend/fleet-spend.query';
import { GatesPanel } from './gates/gates-panel';
import { injectHubDecisionsQuery } from './gates/gates.query';
import {
  injectHubBacklogQuery,
  injectHubQueueQuery,
} from './queue/queue.query';
import { QuestionsPanel } from './questions/questions-panel';
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

/** The rails as the board mounts them. */
@Component({
  selector: 'app-test-board-rails-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QuestionsPanel, GatesPanel],
  template: '<app-questions-panel /><app-gates-panel />',
})
class TestBoardRails {
  readonly chunks = injectHubBoardChunksQuery();
}

/**
 * The routes the board reads, each answered with an empty page but for the rows the rail tests use: the hub answers
 * the questions and decisions reads with the lensed project's rows, here two whose chunk is not on the board's list.
 */
function answer(_method: string, path: string): unknown {
  switch (path) {
    case '/api/chunks':
      return {
        chunks: [{ chunk_id: 'ch_alpha', status: 'needs_human' }],
        next_cursor: null,
      };
    case '/api/chunk-counts':
      return { counts: {} };
    case '/api/queue':
    case '/api/backlog':
      return { entries: [], next_cursor: null };
    case '/api/questions':
      return [
        {
          question_id: 'q_alpha',
          chunk_id: 'ch_alpha',
          question: 'q?',
          options: [],
          runner_id: 'rn_01',
          asked_at: '2026-07-16T00:00:01Z',
        },
        {
          question_id: 'q_beta',
          chunk_id: 'ch_beta',
          question: 'q?',
          options: [],
          runner_id: 'rn_01',
          asked_at: '2026-07-16T00:00:01Z',
        },
        {
          question_id: 'q_gamma',
          chunk_id: 'ch_gamma',
          question: 'q?',
          options: [],
          runner_id: 'rn_01',
          asked_at: '2026-07-16T00:00:01Z',
        },
      ];
    case '/api/decisions':
      return {
        decisions: [
          {
            decision_id: 'd_alpha',
            chunk_id: 'ch_alpha',
            node_name: 'gate',
            choices: [],
          },
          {
            decision_id: 'd_beta',
            chunk_id: 'ch_beta',
            node_name: 'gate',
            choices: [],
          },
          {
            decision_id: 'd_gamma',
            chunk_id: 'ch_gamma',
            node_name: 'gate',
            choices: [],
          },
        ],
      };
    case '/api/activity':
      return { activity: [] };
    default:
      return {};
  }
}

const LENSED_ROUTES = [
  '/api/chunks',
  '/api/chunk-counts',
  '/api/queue',
  '/api/backlog',
  '/api/questions',
  '/api/decisions',
  '/api/activity',
  '/api/spend',
];

describe('the board reads under the project lens', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, answer);
    await TestBed.configureTestingModule({
      imports: [TestBoardReads, TestBoardRails],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(
          new QueryClient({ defaultOptions: { queries: { retry: false } } }),
        ),
        provideRouter([
          {
            path: 'p/:projectSlug',
            children: [{ path: '**', component: TestView }],
          },
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

  it('sends the lensed project on every chunk, count, queue, backlog, question, decision, activity and spend read', async () => {
    await mountAt('/p/alpha/board');

    for (const route of LENSED_ROUTES) {
      const requests = stub.forRoute(route, 'GET');
      expect(requests.length, `${route} was not read`).toBeGreaterThan(0);
      for (const request of requests) {
        expect(
          new URLSearchParams(request.search).get('project'),
          `${route} lost the lens`,
        ).toBe('alpha');
      }
    }
  });

  it('reads the whole tenant on All, naming no project', async () => {
    await mountAt('/board');

    for (const route of LENSED_ROUTES) {
      const requests = stub.forRoute(route, 'GET');
      expect(requests.length, `${route} was not read`).toBeGreaterThan(0);
      for (const request of requests) {
        expect(
          new URLSearchParams(request.search).has('project'),
          `${route} named a project on All`,
        ).toBe(false);
      }
    }
  });

  async function mountRailsAt(url: string) {
    await TestBed.inject(Router).navigateByUrl(url);
    const fixture = TestBed.createComponent(TestBoardRails);
    await settle(fixture);
    return {
      rails: fixture.nativeElement as HTMLElement,
      chunkIds: fixture.componentInstance.chunks
        .data()
        ?.map((chunk) => chunk.chunk_id),
    };
  }

  it('shows every question and decision the hub returns for the lens, whether or not its chunk is on the board list', async () => {
    const { rails, chunkIds } = await mountRailsAt('/p/alpha/board');

    expect(
      rails.querySelectorAll('[data-testid="rail-question"]'),
    ).toHaveLength(3);
    expect(rails.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(3);
    expect(chunkIds).toEqual(['ch_alpha']);
    for (const request of [
      ...stub.forRoute('/api/questions', 'GET'),
      ...stub.forRoute('/api/decisions', 'GET'),
    ]) {
      expect(new URLSearchParams(request.search).get('project')).toBe('alpha');
    }
  });

  it('shows every question and decision on All, naming no project', async () => {
    const { rails } = await mountRailsAt('/board');

    expect(
      rails.querySelectorAll('[data-testid="rail-question"]'),
    ).toHaveLength(3);
    expect(rails.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(3);
    for (const request of [
      ...stub.forRoute('/api/questions', 'GET'),
      ...stub.forRoute('/api/decisions', 'GET'),
    ]) {
      expect(new URLSearchParams(request.search).has('project')).toBe(false);
    }
  });
});
