import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { type Tone, type hubApi, toneColor } from 'fleet';
import { chunkDetail, settle, turnSegmentView } from 'fleet/testing';
import type { LeaseTranscript } from './lease-transcript.query';
import type { PlacedStep, StepRef } from './step.model';
import type { EnvTimelineStepView } from './step.query';
import { StepDrawerView } from './step-drawer-view';

const NOW = Date.parse('2026-07-13T12:00:00Z');

function placed(over: Partial<hubApi.EnvTimelineSegmentView> = {}, host: Partial<PlacedStep> = {}): PlacedStep {
  return {
    environment_id: 'env_a',
    host_id: 'host_01',
    host_name: 'xps',
    workspace_id: 'ws_1',
    segment: {
      chunk_id: 'ch_01',
      cost_estimated: false,
      cost_usd: 0,
      ended_at: '2026-07-13T10:30:00Z',
      epoch: 1,
      escalation: null,
      escalation_open: false,
      executor: 'runner',
      exit_choice: 'pass',
      failed: false,
      graph_id: 'gr_1',
      graph_name: 'delivery',
      lease_id: 'lease_01',
      node_id: 'nd_build',
      node_name: 'build',
      project_id: null,
      runner_id: 'rn_01',
      runner_name: 'runner-one',
      started_at: '2026-07-13T10:00:00Z',
      title: null,
      ...over,
    },
    ...host,
  };
}

const PLAN = placed({ node_name: 'plan', lease_id: 'lease_plan', started_at: '2026-07-13T08:00:00Z', ended_at: '2026-07-13T09:00:00Z', exit_choice: 'go' });
const BUILD = placed();
const VERIFY = placed(
  { node_name: 'verify', lease_id: 'lease_verify', started_at: '2026-07-13T11:00:00Z', ended_at: '2026-07-13T11:30:00Z', exit_choice: null },
  { environment_id: 'env_b' },
);

function stepView(step: PlacedStep, over: Partial<EnvTimelineStepView> = {}): EnvTimelineStepView {
  return {
    artifact_keys: [],
    escalations: [],
    generated_at: '2026-07-13T12:00:00Z',
    invocations: [],
    spend: null,
    journey: [PLAN, BUILD, VERIFY],
    step,
    ...over,
  };
}

const TRANSCRIPT = (turns: NonNullable<LeaseTranscript>['turns'], truncated = false): LeaseTranscript => ({ turns, truncated });

describe('StepDrawerView', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideRouter([])] }));

  async function render(inputs: Record<string, unknown> = {}) {
    const fixture = TestBed.createComponent(StepDrawerView);
    const view = (inputs['view'] as EnvTimelineStepView | undefined) ?? stepView(BUILD);
    fixture.componentRef.setInput('nowMs', NOW);
    fixture.componentRef.setInput('view', view);
    fixture.componentRef.setInput('placed', view.step);
    for (const [name, value] of Object.entries(inputs)) fixture.componentRef.setInput(name, value);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el };
  }

  /** The header badge wearing `tone`'s colour, if any. */
  const badge = (el: HTMLElement, tone: Tone) =>
    [...el.querySelectorAll<HTMLElement>('fleet-kit-badge')].find((b) => b.querySelector('.badge')?.getAttribute('style')?.includes(toneColor(tone)) ?? false) ?? null;
  const q = (el: HTMLElement, testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);

  it('names a leased step by its lease with no hub-step badge', async () => {
    const { el } = await render();

    expect(q(el, 'step-drawer-lease')?.textContent?.trim()).toBe('lease_01');
    expect(badge(el, 'idle')).toBeNull();
  });

  it('names a lease-less step by its node and badges it as a hub step', async () => {
    const hub = placed({ executor: 'hub', lease_id: null, epoch: 4 });
    const { el } = await render({ view: stepView(hub, { journey: [hub] }) });

    expect(q(el, 'step-drawer-lease')?.textContent?.trim()).toBe('build');
    expect(q(el, 'step-drawer-lease')?.getAttribute('title')).toBe('no session of its own — a hub-side step');
    expect(badge(el, 'idle')?.textContent).toContain('hub step');
  });

  it('rests on its own copy, with a close button, for a chunk with no node step yet', async () => {
    const { fixture, el } = await render({ state: 'empty', view: undefined, placed: null });
    let dismissed = 0;
    fixture.componentInstance.dismiss.subscribe(() => (dismissed += 1));

    expect(q(el, 'step-drawer-empty')?.textContent).toContain('NO NODE STEP YET');
    expect(q(el, 'step-drawer-error')).toBeNull();
    expect(q(el, 'step-drawer-lease')).toBeNull();
    q(el, 'step-drawer-close')!.click();
    expect(dismissed).toBe(1);
  });

  it('writes no internal term into any copy it shows an operator', async () => {
    const hub = placed({ executor: 'hub', lease_id: null, epoch: 4, ended_at: null });
    const renders = [
      await render(),
      await render({ view: stepView(hub, { journey: [PLAN, hub] }), transcriptState: 'ready' }),
      await render({ transcriptState: 'ready', transcript: TRANSCRIPT([]) }),
      await render({ transcriptState: 'error' }),
      await render({ transcriptState: 'ready', transcript: null }),
      await render({ view: stepView(placed({ lease_id: null }), { journey: [placed({ lease_id: null })] }) }),
      await render({ view: stepView(placed({ lease_id: null, epoch: null }), { journey: [placed({ lease_id: null, epoch: null })] }) }),
      await render({ state: 'empty', view: undefined, placed: null }),
      await render({ state: 'error', view: undefined, placed: null }),
    ];
    for (const { el } of renders) {
      const copy = [
        el.textContent ?? '',
        ...[...el.querySelectorAll('[title], [aria-label]')].flatMap((n) => [n.getAttribute('title') ?? '', n.getAttribute('aria-label') ?? '']),
      ]
        // An id is shown as itself, whatever its prefix.
        .map((text) => text.replace(/\blease_\w+/g, ''))
        .join('\n');
      expect(copy).not.toMatch(/\b(route|routed|routing|lease|leases|leased|epoch|attempt|tenure|reap|reaped)\b/i);
    }
  });

  it('badges an openly escalated step and a running one', async () => {
    const live = placed({ escalation: 'stuck', escalation_open: true, ended_at: null });
    const { el } = await render({ view: stepView(live, { journey: [live] }) });

    expect(badge(el, 'needs')?.textContent).toContain('escalated · stuck');
    expect(badge(el, 'running')?.textContent).toContain('running');
  });

  it('keeps a superseded escalation in the list, without the badge', async () => {
    const settled = placed({ escalation: 'stuck', escalation_open: false });
    const escalations = [{ cause: 'stuck', detail: null, recorded_at: '2026-07-13T10:10:00Z' }];
    const { el } = await render({ view: stepView(settled, { journey: [settled], escalations }) });

    expect(badge(el, 'needs')).toBeNull();
    expect(el.querySelector('.sd-escalation')?.textContent).toContain('escalated · stuck');
  });

  it('names a runner step with no known session by its node, truthfully and with no hub badge', async () => {
    const unheard = placed({ executor: 'runner', lease_id: null, epoch: null });
    const { el } = await render({ view: stepView(unheard, { journey: [unheard] }), transcriptState: 'empty' });

    expect(q(el, 'step-drawer-lease')?.textContent?.trim()).toBe('build');
    expect(q(el, 'step-drawer-lease')?.getAttribute('title')).toBe("the hub never heard this step's session start");
    expect(badge(el, 'idle')).toBeNull();
    expect(el.querySelector('.sd-transcript')?.textContent).not.toContain('hub-side');
  });

  it('names a forced-entry takeover session by its node, as one that took the chunk over, with no hub badge', async () => {
    const takeover = placed({ executor: 'runner', lease_id: null, epoch: 3 });
    const { el } = await render({ view: stepView(takeover, { journey: [takeover] }), transcriptState: 'ready', transcript: TRANSCRIPT([]) });

    expect(q(el, 'step-drawer-lease')?.textContent?.trim()).toBe('build');
    expect(q(el, 'step-drawer-lease')?.getAttribute('title')).toBe('a session that took the chunk over on a forced entry');
    expect(badge(el, 'idle')).toBeNull();
    expect(el.querySelector('.sd-transcript')?.textContent).not.toContain('hub-side');
  });

  it('shows neither badge on a settled, unescalated step', async () => {
    const { el } = await render();

    expect(badge(el, 'needs')).toBeNull();
    expect(badge(el, 'running')).toBeNull();
  });

  it('names a step on no graph the hub knows by its node alone', async () => {
    const orphan = placed({ graph_id: 'gr_gone', graph_name: null });
    const { el } = await render({ view: stepView(orphan, { journey: [orphan] }) });

    expect(q(el, 'step-drawer-graph')?.textContent?.trim()).toBe('build');
  });

  it('links the chunk to its page carrying the origin params', async () => {
    const { el } = await render({ origin: { layout: 'tracks', from: 'lease_01', fromAt: null } });

    const href = q(el, 'step-drawer-chunk')!.getAttribute('href')!;
    expect(href).toContain('/board/chunk/ch_01');
    expect(href).toContain('layout=tracks');
    expect(href).toContain('from=lease_01');
  });

  it('links the graph when the segment names one, else prints it plain', async () => {
    const { el } = await render();
    expect(q(el, 'step-drawer-graph')?.getAttribute('href')).toContain('/graphs/gr_1');

    const bare = placed({ graph_id: null });
    const { el: plain } = await render({ view: stepView(bare, { journey: [bare] }) });
    expect(q(plain, 'step-drawer-graph')).toBeNull();
    expect(plain.querySelector('.node')?.textContent).toContain('delivery/build');
  });

  it('links the runner under its host, falling back to the runner when hostless', async () => {
    const { el } = await render();
    const link = q(el, 'step-drawer-runner')!;
    expect(link.getAttribute('href')).toContain('/fleet/host_01');
    expect(link.textContent).toContain('xps/runner-one');

    const lone = placed({}, { host_id: null, host_name: null });
    const { el: hostless } = await render({ view: stepView(lone, { journey: [lone] }) });
    const bare = q(hostless, 'step-drawer-runner')!;
    expect(bare.getAttribute('href')).toContain('/fleet/rn_01');
    expect(bare.textContent).toContain('no host/runner-one');
  });

  it('shows the model once the step has landed, a skeleton while it is pending', async () => {
    const invocations: hubApi.EnvTimelineInvocationView[] = [
      { kind: 'spawn', harness_id: 'claude-code', model: 'opus', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 1, estimated_cost_usd: null, recorded_at: '2026-07-13T10:00:00Z' },
    ];
    const { el } = await render({ view: stepView(BUILD, { invocations }) });
    expect(el.querySelector('.model')?.textContent).toContain('claude-code · opus');
    expect(el.querySelector('.sd-sk-model')).toBeNull();

    const { el: pending } = await render({ view: stepView(BUILD, { invocations }), stepPending: true });
    expect(pending.querySelector('.sd-sk-model')).not.toBeNull();
    expect(pending.querySelector('.model')).toBeNull();
  });

  it('draws one block per journey step, marks the current one and spaces the gaps', async () => {
    const { el } = await render();

    const blocks = [...el.querySelectorAll<HTMLElement>('.sd-blk')];
    expect(blocks.map((b) => b.textContent?.trim())).toEqual(['plan', 'build', 'verify']);
    expect(blocks.map((b) => b.classList.contains('sd-blk-current'))).toEqual([false, true, false]);
    expect(el.querySelectorAll('.sd-gap')).toHaveLength(2);
  });

  it('emits the clicked block, and the neighbours, as step refs', async () => {
    const { fixture, el } = await render();
    const picked: StepRef[] = [];
    fixture.componentInstance.selectStep.subscribe((ref) => picked.push(ref));

    el.querySelectorAll<HTMLElement>('.sd-blk')[2].click();
    const [from, onTo] = [...el.querySelectorAll<HTMLElement>('.sd-nb')];
    from.click();
    onTo.click();

    expect(from.textContent).toContain('from plan by go');
    expect(onTo.textContent).toContain('on to verify on env_b');
    expect(picked).toEqual([
      { chunkId: 'ch_01', session: 'lease_verify', at: null },
      { chunkId: 'ch_01', session: 'lease_plan', at: null },
      { chunkId: 'ch_01', session: 'lease_verify', at: null },
    ]);
  });

  it('says where a path ends when there is no next step', async () => {
    const done = stepView(VERIFY);
    const { el } = await render({ view: done });
    expect(el.querySelector('.sd-around .dim')?.textContent).toContain('last step on an environment');

    const live = placed({ ended_at: null });
    const { el: running } = await render({ view: stepView(live, { journey: [live] }) });
    expect(running.querySelector('.sd-around .dim')?.textContent).toContain('still here');
  });

  it('emits dismiss from the close button', async () => {
    const { fixture, el } = await render();
    let dismissed = 0;
    fixture.componentInstance.dismiss.subscribe(() => dismissed++);

    q(el, 'step-drawer-close')!.click();

    expect(dismissed).toBe(1);
  });

  it('lists the step facts', async () => {
    const { el } = await render();

    const facts = q(el, 'step-drawer-facts')!.textContent!;
    expect(facts).toContain('Where');
    expect(facts).toContain('ws_1 / env_a');
    expect(facts).toContain('Held');
    expect(facts).toContain('no usage recorded');
  });

  it('lists escalations only once the step has landed', async () => {
    const escalations = [{ cause: 'stuck', detail: 'no progress', recorded_at: '2026-07-13T10:10:00Z' }];
    const { el } = await render({ view: stepView(BUILD, { escalations }) });
    expect(el.querySelector('.sd-escalation')?.textContent).toContain('escalated · stuck');
    expect(el.querySelector('.sd-escalation')?.textContent).toContain('no progress');

    const { el: pending } = await render({ view: stepView(BUILD, { escalations }), stepPending: true });
    expect(pending.querySelector('.sd-escalation')).toBeNull();
  });

  it('counts the work items from the chunk', async () => {
    const chunk = chunkDetail({
      chunk_id: 'ch_01',
      work_refs: [
        { source: 'widget', ref: '1', label: 'widget#1', web_url: null },
        { source: 'widget', ref: '2', label: 'widget#2', web_url: null },
      ],
    });
    const { el } = await render({ chunk });

    const box = [...el.querySelectorAll('fleet-kit-panel.d-box')].find((p) => p.getAttribute('label') === 'Work item');
    expect(box?.textContent).toContain('2');
  });

  describe('transcript', () => {
    const heading = (el: HTMLElement) => el.querySelector('.sd-transcript fleet-kit-section-head')?.textContent?.trim();
    const section = (el: HTMLElement) => el.querySelector('.sd-transcript')!.textContent!;

    it('explains that a hub-side step has none', async () => {
      const hub = placed({ executor: 'hub', lease_id: null, epoch: 4 });
      const { el } = await render({ view: stepView(hub, { journey: [hub] }), transcriptState: 'ready' });

      expect(section(el)).toContain('a hub-side step — no session, so no transcript');
    });

    it('shows a skeleton while loading', async () => {
      const { el } = await render({ transcriptState: 'loading' });

      expect(q(el, 'step-drawer-transcript-loading')).not.toBeNull();
    });

    it('says when the read failed', async () => {
      const { el } = await render({ transcriptState: 'error' });

      expect(section(el)).toContain('could not read the transcript');
      expect(q(el, 'step-drawer-transcript-loading')).toBeNull();
    });

    it('says quietly when the caller may not read it', async () => {
      const { el } = await render({ transcriptState: 'ready', transcript: null });

      expect(q(el, 'step-drawer-transcript-no-access')?.textContent).toContain('no access to the transcript');
      expect(section(el)).not.toContain('could not read');
    });

    it('says when the step has no transcript to read', async () => {
      const { el } = await render({ transcriptState: 'empty' });

      expect(section(el)).toContain('no transcript to read for this step');
      expect(q(el, 'step-drawer-transcript-loading')).toBeNull();
    });

    it('says when no turns were shipped', async () => {
      const { el } = await render({ transcriptState: 'ready', transcript: TRANSCRIPT([]) });

      expect(section(el)).toContain('no transcript shipped for this session');
      expect(heading(el)).toBe('Transcript');
    });

    it('labels a null executor as unknown and still reads its transcript', async () => {
      const unknown = placed({ executor: null, epoch: 2 });
      const turns = [turnSegmentView({ index: 0 })];
      const { el } = await render({ view: stepView(unknown, { journey: [unknown] }), transcriptState: 'ready', transcript: TRANSCRIPT(turns) });

      expect(section(el)).toContain('executor unknown');
      expect(el.querySelector('fleet-transcript-viewer')).not.toBeNull();
    });

    it('does not label a known executor as unknown', async () => {
      const { el } = await render({ transcriptState: 'ready', transcript: TRANSCRIPT([]) });

      expect(section(el)).not.toContain('executor unknown');
    });

    it('draws the loading, empty and error states through the kit, with the loading testid on the projected skeleton', async () => {
      for (const state of ['loading', 'empty', 'error'] as const) {
        const { el } = await render({ transcriptState: state });
        expect(el.querySelector('.sd-transcript fleet-kit-async-state'), state).not.toBeNull();
      }
      const { el } = await render({ transcriptState: 'error' });
      expect(el.querySelector('.sd-transcript .status.error')?.textContent).toContain('could not read the transcript');
      const loading = await render({ transcriptState: 'loading' });
      expect(q(loading.el, 'step-drawer-transcript-loading')?.closest('fleet-kit-async-state')).not.toBeNull();
    });

    it('renders the viewer with the turns, counts them, and notes a truncation', async () => {
      const turns = [turnSegmentView({ index: 0 }), turnSegmentView({ index: 1, text: 'second' })];
      const { el } = await render({ transcriptState: 'ready', transcript: TRANSCRIPT(turns, true) });

      expect(el.querySelector('fleet-transcript-viewer')).not.toBeNull();
      expect(heading(el)).toBe('Transcript · 2');
      expect(section(el)).toContain('truncated — some turns were dropped');
    });
  });
});
