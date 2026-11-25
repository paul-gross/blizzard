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

import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import {
  chunkCountsView,
  chunkSummary,
  decisionView,
  fleetSpendView,
  questionView,
  type RequestClientStub,
  settle,
  stubHubClient,
} from 'fleet/testing';
import { injectDomainBoardChunksQuery } from '../core/chunks.query';
import { provideProjectLens } from '../core/project-lens';
import { injectHubActivityQuery } from './activity/activity.query';
import { injectHubChunkCountsQuery } from './chunks/chunk-counts.query';
import { injectHubFleetSpendDomainQuery } from './fleet-spend/fleet-spend.query';
import { GatesPanel } from './gates/gates-panel';
import { injectBoardDecisionsQuery } from './gates/gates.query';
import {
  injectHubBacklogQuery,
  injectHubQueueQuery,
} from './queue/queue.query';
import { QuestionsPanel } from './questions/questions-panel';
import { injectBoardQuestionsQuery } from './questions/questions.query';

@Component({ selector: 'app-test-lens-view', template: '' })
class TestView {}

/** Every read the board makes, mounted together. */
@Component({
  selector: 'app-test-board-lens-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestBoardReads {
  readonly chunks = injectDomainBoardChunksQuery();
  readonly counts = injectHubChunkCountsQuery();
  readonly queue = injectHubQueueQuery();
  readonly backlog = injectHubBacklogQuery(() => true);
  readonly questions = injectBoardQuestionsQuery();
  readonly decisions = injectBoardDecisionsQuery();
  readonly activity = injectHubActivityQuery();
  readonly spend = injectHubFleetSpendDomainQuery(() => '2026-10-10T00:00:00Z');
}

/** The rails as the board mounts them. */
@Component({
  selector: 'app-test-board-rails-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QuestionsPanel, GatesPanel],
  template: '<app-questions-panel /><app-gates-panel />',
})
class TestBoardRails {
  readonly chunks = injectDomainBoardChunksQuery();
}

@Component({ selector: 'app-test-rail-observers', template: '' })
class TestRailObservers {
  readonly questions = injectBoardQuestionsQuery();
  readonly moreQuestions = injectBoardQuestionsQuery();
  readonly decisions = injectBoardDecisionsQuery();
  readonly moreDecisions = injectBoardDecisionsQuery();
}

/**
 * The routes the board reads, each answered with an empty page but for the rows the rail tests use: the hub answers
 * the questions and decisions reads with the lensed project's rows, here two whose chunk is not on the board's list.
 */
const ROUTES: Parameters<typeof stubHubClient>[0] = {
  'GET /api/chunks': () => ({
    chunks: [chunkSummary({ chunk_id: 'ch_alpha', graph_id: 'gr_1', status: 'needs_human' })],
    next_cursor: null, board_done_policy: null,
  }),
  'GET /api/chunk-counts': () => chunkCountsView(),
  'GET /api/queue': () => ({ entries: [], next_cursor: null }),
  'GET /api/backlog': () => ({ entries: [], next_cursor: null }),
  'GET /api/questions': () => [
    questionView({
      question_id: 'q_alpha',
      chunk_id: 'ch_alpha',
      question: 'q?',
      options: [],
      runner_id: 'rn_01',
      asked_at: '2026-07-16T00:00:01Z',
    }),
    questionView({
      question_id: 'q_beta',
      chunk_id: 'ch_beta',
      question: 'q?',
      options: [],
      runner_id: 'rn_01',
      asked_at: '2026-07-16T00:00:01Z',
    }),
    questionView({
      question_id: 'q_gamma',
      chunk_id: 'ch_gamma',
      question: 'q?',
      options: [],
      runner_id: 'rn_01',
      asked_at: '2026-07-16T00:00:01Z',
    }),
  ],
  'GET /api/decisions': () => ({
    decisions: [
      decisionView({ decision_id: 'd_alpha', chunk_id: 'ch_alpha', node_name: 'gate', choices: [] }),
      decisionView({ decision_id: 'd_beta', chunk_id: 'ch_beta', node_name: 'gate', choices: [] }),
      decisionView({ decision_id: 'd_gamma', chunk_id: 'ch_gamma', node_name: 'gate', choices: [] }),
    ],
  }),
  'GET /api/activity': () => ({ activity: [] }),
  'GET /api/spend': () => fleetSpendView(),
};

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
    stub = stubHubClient(ROUTES);
    await TestBed.configureTestingModule({
      imports: [TestBoardReads, TestBoardRails, TestRailObservers],
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

  it('shares each lensed wire cache entry between domain observers and retains the polling backstop', async () => {
    const question = questionView({ question_id: 'q_shared', question: 'Which?', options: ['yes'] });
    const decision = decisionView({ decision_id: 'd_shared' });
    stub.restore();
    stub = stubHubClient({
      'GET /api/questions': () => [question],
      'GET /api/decisions': () => ({ decisions: [decision] }),
    });
    await TestBed.inject(Router).navigateByUrl('/p/alpha/board');
    const fixture = TestBed.createComponent(TestRailObservers);
    await settle(fixture);

    const client = TestBed.inject(QueryClient);
    const questionKey = ['hub', 'questions', { project: 'alpha' }];
    const decisionKey = ['hub', 'decisions', { project: 'alpha' }];
    expect(stub.forRoute('/api/questions', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/decisions', 'GET')).toHaveLength(1);
    expect(client.getQueryData(questionKey)).toEqual([question]);
    expect(client.getQueryData(decisionKey)).toEqual([decision]);
    expect(client.getQueryCache().getAll()).toHaveLength(2);
    for (const key of [questionKey, decisionKey]) {
      const query = client.getQueryCache().find({ queryKey: key, exact: true })!;
      expect(query.getObserversCount()).toBe(2);
      for (const observer of query.observers) {
        expect(observer.options.refetchInterval).toBe(LIVE_COVERED_POLL_BACKSTOP_MS);
      }
    }
    expect(fixture.componentInstance.questions.data()).toEqual([{
      chunk_id: question.chunk_id,
      question: 'Which?',
      question_id: 'q_shared',
      runner_id: question.runner_id,
      runner_name: question.runner_name,
      options: ['yes'],
    }]);
    expect(fixture.componentInstance.moreQuestions.data()).toEqual(fixture.componentInstance.questions.data());
    expect(fixture.componentInstance.decisions.data()).toEqual([{
      chunk_id: decision.chunk_id,
      decision_id: 'd_shared',
      node_name: decision.node_name,
      imposed_by_runner_id: decision.imposed_by_runner_id,
      imposed_by_runner_name: decision.imposed_by_runner_name,
      choices: ['approve', 'rework'],
    }]);
    expect(fixture.componentInstance.moreDecisions.data()).toEqual(fixture.componentInstance.decisions.data());
  });

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
        ?.chunks.map((chunk) => chunk.chunk_id),
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
