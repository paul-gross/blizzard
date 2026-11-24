import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  type RequestClientStub,
  chunkDetail,
  decisionView,
  hostRunnerView,
  questionView,
  runnerLeaseView,
  settle,
  stubRunnerClient,
} from 'fleet/testing';

import { RunnerChunkPage } from './runner-chunk-page';

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

/** A chunk parked on an open question with options, beside an answered one, and an open gate with its checks. */
const DETAIL = chunkDetail({
  chunk_id: CHUNK_ID,
  status: 'waiting_on_human',
  questions: [
    questionView({ question_id: 'qn_open', chunk_id: CHUNK_ID, question: 'What next?', options: ['ship', 'hold'], answered: false }),
    questionView({
      question_id: 'qn_answered',
      chunk_id: CHUNK_ID,
      question: 'Earlier question?',
      answered: true,
      answer: 'Yes',
      answered_by: 'alice',
      answered_at: '2026-07-16T11:10:00.000Z',
    }),
  ],
  decision: decisionView({
    decision_id: 'de_01',
    chunk_id: CHUNK_ID,
    node_id: 'nd_review',
    node_name: 'review',
    choices: [
      { name: 'approve', description: 'Approve', requires_checks: false, would_override_red_checks: false },
      { name: 'reject', description: 'Reject', requires_checks: false, would_override_red_checks: false },
    ],
    check_results: [{ command: 'npm test', passed: false }],
    transitioned: false,
  }),
});

const ROUTES = {
  'GET /api/host/runners': () => ({
    items: [hostRunnerView({ bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }], agents_in_use: 1, max_agents: 4 })],
  }),
  'GET /api/host/environments': () => ({ workspaces: [] }),
  'GET /runners/{runner_id}/api/leases': () => ({
    items: [
      runnerLeaseView({
        lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
        chunk_id: CHUNK_ID,
        node_id: 'nd_review',
        node_name: 'review',
        created_at: '2026-07-16T11:00:00.000Z',
        last_heartbeat_at: '2026-07-16T11:59:26.000Z',
      }),
    ],
  }),
  'GET /runners/{runner_id}/api/chunks/{chunk_id}': () => DETAIL,
  'GET /runners/{runner_id}/api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
  'GET /runners/{runner_id}/api/chunks/{chunk_id}/transcripts': () => ({ chunk_id: CHUNK_ID, segments: [] }),
};

describe('RunnerChunkPage', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRunnerClient(ROUTES);
    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board/chunk/:chunkId', component: RunnerChunkPage }]),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('renders the asks, the answered trail, and the gate read-only, sending no answer or resolution', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);
    const el = harness.fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="question-text"]')?.textContent).toContain('What next?');
    expect(el.querySelector('[data-testid="answered-question-text"]')?.textContent).toContain('Earlier question?');
    expect(el.querySelector('[data-testid="answered-by"]')?.textContent).toContain('alice');
    expect(el.querySelector('[data-testid="decision-node"]')?.textContent).toContain('review');
    expect(el.querySelector('[data-testid="decision-check"]')?.textContent).toContain('npm test');
    for (const id of ['answer-input', 'answer-submit', 'question-option', 'decision-note-input', 'decision-choice']) {
      expect(el.querySelector(`[data-testid="${id}"]`), id).toBeNull();
    }

    await new Promise((resolve) => setTimeout(resolve, 0));
    await settle(harness.fixture);
    expect(stub.requests.filter((request) => request.method !== 'GET')).toEqual([]);
  });
});
