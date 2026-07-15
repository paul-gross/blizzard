import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailPanel, type PmItemsState } from './chunk-detail-panel';

const ISSUE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [
    { provider: 'github', url: 'https://github.com/acme/widget/issues/42', label: 'gh:widget#42' },
  ],
  history: [],
  artifacts: [],
};

const REVIEW_FAIL_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  pm_pointers: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' },
    { from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:02Z' },
  ],
  artifacts: [
    {
      key: 'build.widget.1',
      kind: 'git_commit',
      name: 'widget',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'acme/widget',
      branch_name: 'b',
      commit_hash: 'c1',
    },
    {
      key: 'review.review-findings.2',
      kind: 'asset',
      name: 'review-findings',
      node_id: 'nd_review',
      node_name: 'review',
      epoch: 2,
      content: 'BLOCKING: the widget endpoint returns 500 on empty input; add a guard.',
    },
  ],
};

const NAMED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01named000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  pm_pointers: [],
  history: [
    {
      from_node_id: 'nd_build',
      from_node_name: 'build',
      to_node_id: 'nd_review',
      to_node_name: 'code-review',
      choice_name: 'pass',
      epoch: 1,
      recorded_at: '2026-07-13T00:00:01Z',
    },
  ],
  artifacts: [
    {
      key: 'build.widget.1',
      kind: 'git_commit',
      name: 'widget',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'acme/widget',
      branch_name: 'feature/widget',
      commit_hash: 'c1',
      branch_url: 'https://forge.example/acme/widget/tree/feature/widget',
    },
    {
      key: 'build.orphan.1',
      kind: 'git_commit',
      name: 'orphan',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'acme/orphan',
      branch_name: 'feature/orphan',
      commit_hash: 'c2',
      branch_url: null,
    },
  ],
};

const WAITING_QUESTION_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01ask00000000000000000000000',
  graph_id: 'gr_1',
  status: 'waiting_on_human',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [],
  history: [],
  artifacts: [],
  questions: [
    {
      question_id: 'qn_01',
      chunk_id: 'ch_01ask00000000000000000000000',
      question: 'Which API style should the endpoint use?',
      options: ['rest', 'graphql'],
      epoch: 1,
      runner_id: 'rn_01',
      session_id: 'se_01',
      asked_at: '2026-07-13T00:00:01Z',
      answered: false,
    },
  ],
};

const WAITING_DECISION_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01gate0000000000000000000000',
  graph_id: 'gr_1',
  status: 'waiting_on_human',
  current_node_id: 'nd_gate',
  latest_epoch: 1,
  pm_pointers: [],
  history: [],
  artifacts: [],
  decision: {
    decision_id: 'de_01',
    chunk_id: 'ch_01gate0000000000000000000000',
    node_id: 'nd_gate',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-13T00:00:01Z',
    choices: [
      { name: 'approve', description: 'Ship it.' },
      { name: 'reject', description: 'Send it back.' },
    ],
    transitioned: false,
  },
};

const ESCALATED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  status: 'needs_human',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  pm_pointers: [],
  history: [],
  artifacts: [],
  escalation: {
    epoch: 3,
    takeover_command: 'blizzard runner takeover ch_01esc00000000000000000000000',
  },
};

describe('ChunkDetailPanel', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkDetailPanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the review-fail loop and the review-findings asset content (MVP criterion 9/11)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The transition history reads oldest-first and shows both edges, including the fail loop.
    const steps = el.querySelectorAll('[data-testid="history-step"]');
    expect(steps).toHaveLength(2);
    expect(steps[0].textContent).toContain('nd_review');
    const failStep = el.querySelector('[data-testid="history-step"][data-choice="fail"]');
    expect(failStep?.textContent).toContain('nd_review');
    expect(failStep?.textContent).toContain('nd_build');
    expect(failStep?.querySelector('[data-testid="history-choice"]')?.textContent).toContain('fail');

    // The review-findings asset content is shown inline.
    const findings = el.querySelector('[data-kind="asset"] [data-testid="artifact-content"]');
    expect(findings?.textContent).toContain('BLOCKING: the widget endpoint returns 500');

    // The git-commit artifact shows its pinned reference, not code.
    const commitRef = el.querySelector('[data-kind="git_commit"] [data-testid="artifact-ref"]');
    expect(commitRef?.textContent).toContain('acme/widget');
    expect(commitRef?.textContent).toContain('c1');
  });

  it('renders human node names on transitions, keeping the raw id as a tooltip (issue #23)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', NAMED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const step = el.querySelector('[data-testid="history-step"]')!;
    // The visible text is the human graph names, not the nd_ ULIDs.
    expect(step.querySelector('.from')?.textContent?.trim()).toBe('build');
    expect(step.querySelector('.to')?.textContent?.trim()).toBe('code-review');
    expect(step.textContent).not.toContain('nd_');
    // The raw node id stays reachable as the label's title.
    expect(step.querySelector('.from')?.getAttribute('title')).toBe('nd_build');
    expect(step.querySelector('.to')?.getAttribute('title')).toBe('nd_review');
  });

  it('falls back to the raw node id when a transition has no resolved name', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const step = el.querySelector('[data-testid="history-step"]')!;
    expect(step.querySelector('.to')?.textContent?.trim()).toBe('nd_review');
  });

  it('shows the artifact branch name and links it to the forge, degrading when no url (issue #23)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', NAMED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const [linked, orphan] = [...el.querySelectorAll('[data-kind="git_commit"] [data-testid="artifact-ref"]')];
    // A derivable branch url renders as a link to the branch on the forge.
    const link = linked.querySelector<HTMLAnchorElement>('a[data-testid="artifact-branch"]');
    expect(link?.textContent?.trim()).toBe('feature/widget');
    expect(link?.getAttribute('href')).toBe('https://forge.example/acme/widget/tree/feature/widget');
    // No url degrades gracefully: the branch name shows as plain text, no broken link.
    expect(orphan.querySelector('a')).toBeNull();
    expect(orphan.querySelector('[data-testid="artifact-branch"]')?.textContent?.trim()).toBe('feature/orphan');
  });

  it('surfaces a waiting_on_human chunk’s open question and its options (MVP criterion 7)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const awaiting = el.querySelector('[data-testid="awaiting-human"]');
    expect(awaiting).not.toBeNull();
    expect(el.querySelector('[data-testid="question-text"]')?.textContent).toContain(
      'Which API style should the endpoint use?',
    );
    const options = [...el.querySelectorAll('[data-testid="question-option"]')].map((o) => o.textContent?.trim());
    expect(options).toEqual(['rest', 'graphql']);
    // No gate on a question-only park.
    expect(el.querySelector('[data-testid="open-decision"]')).toBeNull();
  });

  it('surfaces a waiting_on_human chunk’s open gate decision and its choices (MVP criterion 12)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_DECISION_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="awaiting-human"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="decision-node"]')?.textContent).toContain('approve-gate');
    const choices = [...el.querySelectorAll('[data-testid="decision-choice"]')].map((c) => c.textContent?.trim());
    expect(choices).toEqual(['approve', 'reject']);
    expect(el.querySelector('[data-testid="open-question"]')).toBeNull();
  });

  it('shows no awaiting-human section when the chunk is not parked', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="awaiting-human"]')).toBeNull();
  });

  it('emits answerQuestion with the typed answer when Answer is activated (MVP criterion 7)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    let emitted: { questionId: string; answer: string; chunkId: string } | undefined;
    fixture.componentInstance.answerQuestion.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();

    expect(emitted).toEqual({
      questionId: 'qn_01',
      answer: 'rest',
      chunkId: 'ch_01ask00000000000000000000000',
    });
  });

  it('emits answerQuestion when an option chip is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    let emitted: { questionId: string; answer: string } | undefined;
    fixture.componentInstance.answerQuestion.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="question-option"]')?.click();
    expect(emitted?.answer).toBe('rest');
  });

  it('does not emit answerQuestion for a blank answer', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    let emitted = false;
    fixture.componentInstance.answerQuestion.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    expect(emitted).toBe(false);
  });

  it('emits resolveDecision with the chosen gate choice when a choice button is clicked (D-042)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_DECISION_DETAIL);
    let emitted: { decisionId: string; choice: string; chunkId: string } | undefined;
    fixture.componentInstance.resolveDecision.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const buttons = el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]');
    buttons[1].click(); // reject

    expect(emitted).toEqual({
      decisionId: 'de_01',
      choice: 'reject',
      chunkId: 'ch_01gate0000000000000000000000',
    });
  });

  it('surfaces an escalation with its copyable takeover command (D-009)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ESCALATED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'blizzard runner takeover ch_01esc',
    );
    expect(el.querySelector('[data-testid="copy-takeover"]')).not.toBeNull();
  });

  it('emits dismiss when the close button is activated', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    let closed = false;
    fixture.componentInstance.dismiss.subscribe(() => (closed = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detail-close"]')?.click();
    expect(closed).toBe(true);
  });

  // --- The Issue tab (issue #24) --------------------------------------------

  async function openIssueTab(pmItems: PmItemsState) {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('pmItems', pmItems);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="tab-issue"]')?.click();
    await fixture.whenStable();
    return el;
  }

  it('keeps the issue content on its own tab — chunk detail is not replaced (AC3)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    // Detail is the default tab: the chunk detail sections render, the Issue pane does not.
    expect(el.querySelector('[data-testid="issue-pane"]')).toBeNull();
    expect(el.querySelector('[aria-label="Node history"]')).not.toBeNull();
    // The Issue tab badges the pointer count from the chunk aggregate.
    expect(el.querySelector('[data-testid="issue-count"]')?.textContent).toContain('1');
  });

  it('renders the issue description and messages on the Issue tab (AC2)', async () => {
    const el = await openIssueTab({
      status: 'success',
      items: [
        {
          provider: 'github',
          url: 'https://github.com/acme/widget/issues/42',
          label: 'gh:widget#42',
          fetched_at: '2026-07-15T00:00:00Z',
          body: 'the widget flake reproduces under load',
          comments: ['seen it too', 'repro attached'],
          error: null,
        },
      ],
    });
    expect(el.querySelector('[data-testid="issue-pane"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-label"]')?.textContent).toContain('gh:widget#42');
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
    const messages = [...el.querySelectorAll('[data-testid="issue-message"]')].map((m) => m.textContent?.trim());
    expect(messages).toEqual(['seen it too', 'repro attached']);
    // The chunk-detail sections are hidden while the Issue tab is open — a tab, not a merge.
    expect(el.querySelector('[aria-label="Node history"]')).toBeNull();
  });

  it('shows one entry per pointer for a grouped chunk (AC4)', async () => {
    const el = await openIssueTab({
      status: 'success',
      items: [
        { provider: 'github', url: 'https://github.com/acme/widget/issues/42', label: 'gh:widget#42', fetched_at: 't', body: 'first', comments: [] },
        { provider: 'github', url: 'https://github.com/acme/widget/issues/43', label: 'gh:widget#43', fetched_at: 't', body: 'second', comments: [] },
      ],
    });
    const items = el.querySelectorAll('[data-testid="issue-item"]');
    expect(items).toHaveLength(2);
    const bodies = [...el.querySelectorAll('[data-testid="issue-body"]')].map((b) => b.textContent?.trim());
    expect(bodies).toEqual(['first', 'second']);
  });

  it('shows an empty state when the chunk has no linked issue (AC4)', async () => {
    const el = await openIssueTab({ status: 'success', items: [] });
    expect(el.querySelector('[data-testid="issue-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-item"]')).toBeNull();
  });

  it('degrades a single unreachable pointer to an inline notice (AC5)', async () => {
    const el = await openIssueTab({
      status: 'success',
      items: [
        { provider: 'github', url: 'https://github.com/acme/widget/issues/42', label: 'gh:widget#42', fetched_at: 't', body: 'reachable', comments: [] },
        { provider: 'github', url: 'https://github.com/acme/widget/issues/43', label: 'gh:widget#43', fetched_at: 't', body: null, comments: [], error: 'forge unreachable for issues/43' },
      ],
    });
    // The reachable pointer still renders its body beside the failed pointer's notice.
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reachable');
    expect(el.querySelector('[data-testid="issue-item-error"]')?.textContent).toContain('forge unreachable');
  });

  it('shows a visible notice when the whole forge read fails (AC5)', async () => {
    const el = await openIssueTab({ status: 'error', items: [] });
    expect(el.querySelector('[data-testid="issue-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-body"]')).toBeNull();
  });
});
