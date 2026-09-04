import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import type { ChunkDetail as ChunkDetailModel } from '../api/hub';
import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { OPERATOR_ME_RESPONSE } from '../testing/auth-fixtures';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { ChunkDetail } from './chunk-detail';

const ROUTED_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_routed',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: [] },
};

const GATE_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_gate',
  graph_id: 'gr_1',
  status: 'waiting_on_human',
  current_node_id: 'nd_gate',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  decision: {
    decision_id: 'de_42',
    chunk_id: 'ch_gate',
    node_id: 'nd_gate',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-13T00:00:01Z',
    choices: [
      { name: 'approve', description: 'Ship it.' },
      { name: 'reject', description: 'Send it back.' },
    ],
    transitioned: false,
    docket: [
      {
        proposal_id: 'wip_01',
        node_name: 'build',
        kind: 'create',
        payload: { kind: 'create', title: 'fix it', body: 'do it', stated_priority: 'normal' },
      },
    ],
  },
};

// A chunk carrying an open pause fact while its derived status reads waiting_on_human —
// the overlap PAUSED's position below the human-gated states creates (issue #46).
const PAUSED_ASKING_DETAIL: ChunkDetailModel = {
  ...GATE_DETAIL,
  chunk_id: 'ch_paused',
  pause: { by: 'operator', set_at: '2026-07-16T00:00:00Z' },
  decision: undefined,
};

// A chunk parked on an open question — the answer-race surface (issue #165).
const ASK_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_ask',
  graph_id: 'gr_1',
  status: 'waiting_on_human',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  questions: [
    {
      question_id: 'qn_77',
      chunk_id: 'ch_ask',
      question: 'Which API style?',
      options: [],
      epoch: 1,
      runner_id: 'rn_01',
      asked_at: '2026-07-13T00:00:01Z',
      answered: false,
    },
  ],
};

// The same chunk after somebody else's answer won the CAS — what the re-read returns.
const ASK_ANSWERED_DETAIL: ChunkDetailModel = {
  ...ASK_DETAIL,
  status: 'running',
  questions: [
    { ...ASK_DETAIL.questions![0], answered: true, answer: 'rest', answered_by: 'alice', delivered: false },
  ],
};

// A not_ready chunk — the one window issue #27's graph edit is open.
const NOT_READY_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_ready',
  graph_id: 'gr_default',
  status: 'not_ready',
  current_node_id: null,
  latest_epoch: null,
  work_refs: [],
  history: [],
  artifacts: [],
};

describe('ChunkDetail container', () => {
  let stub: RequestClientStub;
  // Mutated per-test to drive the detach mutation's response (200/404/409); the stub
  // closure below reads it live, so a test can set it after the fixture is mounted.
  let detachResponse: unknown = {};
  // The same, for the pause/resume verbs (issue #46).
  let pauseResponse: unknown = {};
  // The same, for the complete verb (issue #294).
  let completeResponse: unknown = {};
  // The same, for the graph edit (issue #27) — it collapses onto the one
  // `PATCH /api/chunks/{id}` call (issue #104), so one variable drives it.
  let editPatchResponse: unknown = {};
  // The same, for the answer verb (issue #165) — 201 winner vs. 409 loser.
  let answerResponse: unknown = {};
  // The same, for declare/release (issue #461).
  let declareResponse: unknown = {};
  let releaseResponse: unknown = {};
  // Whether the chunk read for `ch_ask` has been answered yet, so a test can make the
  // post-answer re-read return the settled row the way the live hub would.
  let askAnswered = false;

  beforeEach(async () => {
    detachResponse = {};
    pauseResponse = {};
    completeResponse = {};
    editPatchResponse = {};
    answerResponse = {};
    declareResponse = {};
    releaseResponse = {};
    askAnswered = false;
    // The generated client's transport is stubbed so we can assert the exact call the button fires.
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/chunks/ch_gate') return GATE_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_routed') return ROUTED_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_paused') return PAUSED_ASKING_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_ready') return NOT_READY_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_ask') return askAnswered ? ASK_ANSWERED_DETAIL : ASK_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_missing') return stubError(404, { detail: 'unknown chunk' });
      if (method === 'POST' && path === '/api/questions/qn_77/answers') return answerResponse;
      if (method === 'POST' && (path === '/api/chunks/ch_routed/pause' || path === '/api/chunks/ch_paused/resume')) {
        return pauseResponse;
      }
      if (method === 'PATCH' && path === '/api/chunks/ch_ready') return editPatchResponse;
      if (method === 'GET' && path.endsWith('/work-items')) {
        return {
          items: [
            {
              source: 'widget',
              ref: '42',
              label: 'widget#42',
              web_url: 'https://github.com/acme/widget/issues/42',
              fetched_at: '2026-07-15T00:00:00Z',
              body: 'the widget flake reproduces under load',
              comments: ['seen it too'],
              error: null,
            },
          ],
        };
      }
      if (path === '/api/decisions/de_42/resolutions') {
        return { decision_id: 'de_42', choice: 'approve', resolved_at: 'x', resolved_by: 'operator' };
      }
      if (method === 'POST' && path === '/api/chunks/ch_routed/detach') return detachResponse;
      if (method === 'POST' && path === '/api/chunks/ch_routed/complete') return completeResponse;
      if (method === 'POST' && path === '/api/chunks/ch_routed/dependencies') return declareResponse;
      if (method === 'POST' && path === '/api/chunks/ch_ready/dependencies') return declareResponse;
      if (method === 'POST' && path === '/api/chunks/ch_routed/dependencies/release') return releaseResponse;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [ChunkDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('holds an empty rest state — not the detail panel — while no chunk is selected (issue #21)', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    // chunkId defaults to null: the dock stays mounted but empty.
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    const rest = el.querySelector('[data-testid="chunk-detail-empty"]');
    expect(rest?.textContent).toContain('SELECT');
  });

  it('renders an error state, not an endless LOADING…, when the detail read fails', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_missing');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-detail-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-detail-loading"]')).toBeNull();
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
  });

  it('fires the resolve-decision client call when a gate choice button is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const buttons = [...el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]')];
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(['approve', 'reject']);

    buttons[0].click();
    await settle(fixture);

    const calls = stub.forRoute('/api/decisions/de_42/resolutions', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ choice: 'approve', struck: [] });
  });

  it('forwards the docket’s toggled proposal ids to the resolve-decision client call', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLInputElement>('[data-testid="docket-strike"]')?.click();
    el.querySelector<HTMLButtonElement>('[data-testid="decision-choice"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/decisions/de_42/resolutions', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ choice: 'approve', struck: ['wip_01'] });
  });

  it('fetches the chunk’s work items through the generated client and renders them in the work-item column (issue #24)', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // It went through the real pass-through route (bzh:generated-client), no hand-written fetch.
    expect(stub.forRoute('/api/chunks/ch_gate/work-items', 'GET')).toHaveLength(1);
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
    expect(el.querySelector('[data-testid="issue-message"]')?.textContent).toContain('seen it too');
  });

  // --- Detach (issue #42) ---------------------------------------------

  it('fires the detach client call for a routed chunk once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_routed/detach', 'POST')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('surfaces the 409 "no live route" response rather than swallowing it', async () => {
    detachResponse = stubError(409, { detail: 'chunk ch_routed has no live route' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_routed/detach', 'POST')).toHaveLength(1);
    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('has no live route');
    confirmSpy.mockRestore();
  });

  it('clears a stale detach error when a different chunk is opened', async () => {
    detachResponse = stubError(409, { detail: 'chunk ch_routed has no live route' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    let el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="action-error"]')).not.toBeNull();

    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="action-error"]')).toBeNull();
    confirmSpy.mockRestore();
  });

  // --- Complete (issue #294) -------------------------------------------

  it('fires the complete client call for a chunk once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_routed/complete', 'POST')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('surfaces a complete failure rather than swallowing it', async () => {
    completeResponse = stubError(404, { detail: 'unknown chunk ch_routed' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_routed/complete', 'POST')).toHaveLength(1);
    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('unknown chunk');
    confirmSpy.mockRestore();
  });

  // --- Declare/release (issue #461) -------------------------------------

  /** Type a prerequisite id into the dock's declare/release field. */
  async function enterPrerequisite(
    fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>,
    prerequisiteChunkId: string,
  ): Promise<HTMLElement> {
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector<HTMLInputElement>('[data-testid="dependency-prerequisite-input"]')!;
    input.value = prerequisiteChunkId;
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    return el;
  }

  it('fires the declare client call once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready'); // not_ready — a status Declare is actually offered on
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_ready/dependencies', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ prerequisite_chunk_id: 'ch_prereq', by: 'operator' });
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator declines the declare confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_ready/dependencies', 'POST')).toHaveLength(0);
    confirmSpy.mockRestore();
  });

  it('fires the release client call once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="release-dependency"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_routed/dependencies/release', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ prerequisite_chunk_id: 'ch_prereq', by: 'operator' });
    confirmSpy.mockRestore();
  });

  it('surfaces the dependent-not-editable 409 refusal in the action notice', async () => {
    declareResponse = stubError(409, { detail: 'dependent chunk is not editable at this status' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('not editable at this status');
    confirmSpy.mockRestore();
  });

  it('surfaces the would-close-a-cycle 409 refusal in the action notice', async () => {
    declareResponse = stubError(409, {
      detail: 'declaring this edge would close a cycle in the standing dependency graph',
    });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('would close a cycle');
    confirmSpy.mockRestore();
  });

  it('surfaces the ephemeral-prerequisite 409 refusal in the action notice', async () => {
    declareResponse = stubError(409, {
      detail: 'prerequisite chunk is ephemeral and cannot be named as a prerequisite',
    });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('ephemeral');
    confirmSpy.mockRestore();
  });

  it('surfaces the unknown-chunk 404 refusal in the action notice', async () => {
    declareResponse = stubError(404, { detail: 'unknown chunk ch_prereq' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('unknown chunk ch_prereq');
    confirmSpy.mockRestore();
  });

  it('surfaces the no-standing-dependency 409 refusal from release in the action notice', async () => {
    releaseResponse = stubError(409, { detail: 'no standing dependency to release' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = await enterPrerequisite(fixture, 'ch_prereq');

    el.querySelector<HTMLButtonElement>('[data-testid="release-dependency"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('no standing dependency');
    confirmSpy.mockRestore();
  });

  // --- Answering a question, and losing the race for it (issue #165) ---------

  /** Type an answer into the dock and submit it. */
  async function answerFrom(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>): Promise<HTMLElement> {
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'graphql';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    await settle(fixture);
    return el;
  }

  it('renders the winner’s name and answer as an outcome when the answer race is lost', async () => {
    // The hub's first-write-wins 409 body is the *winning row*, not a `{detail}` error —
    // folding it through errorMessage() showed the loser a generic failure instead of the
    // one thing worth saying: who answered, and what they said.
    answerResponse = stubError(409, {
      won: false,
      question_id: 'qn_77',
      answer: 'rest',
      answered_by: 'alice',
      answered_at: '2026-07-13T00:01:00Z',
    });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ask');
    await settle(fixture);
    askAnswered = true; // the race was lost, so the re-read now sees alice's answer

    const el = await answerFrom(fixture);

    const outcome = el.querySelector('[data-testid="action-outcome"]');
    expect(outcome?.textContent).toContain('alice');
    expect(outcome?.textContent).toContain('rest');
    // An outcome, not a failure: the error notice stays empty.
    expect(el.querySelector('[data-testid="action-error"]')).toBeNull();
    // And the losing attempt still re-read the chunk, so the question now renders
    // answered with its trail rather than sitting on the stale open row.
    expect(el.querySelector('[data-testid="open-question"]')).toBeNull();
    expect(el.querySelector('[data-testid="answered-by"]')?.textContent).toContain('alice');
  });

  it('surfaces a genuine answer failure on the error channel, not the outcome one', async () => {
    answerResponse = stubError(404, { detail: 'unknown question qn_77' });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ask');
    await settle(fixture);

    const el = await answerFrom(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('unknown question');
    expect(el.querySelector('[data-testid="action-outcome"]')).toBeNull();
  });

  it('clears a stale answer outcome when a different chunk is opened', async () => {
    answerResponse = stubError(409, {
      won: false,
      question_id: 'qn_77',
      answer: 'rest',
      answered_by: 'alice',
      answered_at: '2026-07-13T00:01:00Z',
    });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ask');
    await settle(fixture);
    let el = await answerFrom(fixture);
    expect(el.querySelector('[data-testid="action-outcome"]')).not.toBeNull();

    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="action-outcome"]')).toBeNull();
  });

  it('clears a stale answer outcome when any other dock action fires', async () => {
    // Otherwise a later action's failure renders the red notice *beside* the leftover
    // cyan "alice answered first", which reads as though the two are about each other.
    answerResponse = stubError(409, {
      won: false,
      question_id: 'qn_77',
      answer: 'rest',
      answered_by: 'alice',
      answered_at: '2026-07-13T00:01:00Z',
    });
    pauseResponse = stubError(409, { detail: 'chunk is already paused' });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ask');
    await settle(fixture);
    const el = await answerFrom(fixture);
    expect(el.querySelector('[data-testid="action-outcome"]')).not.toBeNull();

    // A sibling action on the same chunk — fired through its handler, since the dock's
    // pause control is not rendered for a waiting_on_human chunk.
    (fixture.componentInstance as unknown as { onPause(id: string): void }).onPause('ch_routed');
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-outcome"]')).toBeNull();
    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('already paused');
  });

  // --- Pause / Resume (issue #46) --------------------------------------------

  it('fires the pause client call for a running chunk once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_routed/pause', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ by: 'operator' });
    confirmSpy.mockRestore();
  });

  it('fires the resume client call for a paused chunk whose status reads waiting_on_human (issue #46)', async () => {
    // The overlap, end to end through the generated client: the dock reads the pause
    // fact off ChunkDetail, so it offers Resume for a chunk whose status hides the pause.
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_paused');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-status"]')?.textContent).toContain('waiting_on_human');
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_paused/resume', 'POST')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('surfaces a 409 refusal from pause in the shared notice rather than swallowing it', async () => {
    pauseResponse = stubError(409, { detail: 'chunk ch_routed is not pausable (delivering)' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('not pausable');
    confirmSpy.mockRestore();
  });

  // --- Graph edit (issue #27; the model edit beside it retired with `Chunk.model`,
  // issue #144) -------------------------------------------------------------

  it('fires the graph edit client call for a not_ready chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_ready', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ graph_id: 'gr_alt' });
  });

  it('offers no graph edit input for a chunk that has left not_ready', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-input"]')).toBeNull();
  });

  it('surfaces a 409 refusal from the graph edit rather than swallowing it', async () => {
    editPatchResponse = stubError(409, { detail: 'chunk ch_ready is already ready' });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('already ready');
  });
});
