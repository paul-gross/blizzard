import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail } from '../../api/hub';
import { RUNNER_HOST_NAMES, RUNNER_NAMES } from '../../core/runner-display-name';
import { chunkDetail, chunkUsageView, transitionView } from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';

const RUNNER_ID = 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3';

const STEP_USAGE = chunkUsageView({
  node_id: 'nd_build',
  epoch: 1,
  kind: 'spawn',
  input_tokens: 1200,
  output_tokens: 800,
  cache_read_tokens: 300,
  cache_create_tokens: 100,
  cost_usd: 0.42,
});

// Two steps, only the first with usage — the second's stamp spans its main row alone.
const COST_DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01cost00000000000000000000000',
  status: 'running',
  current_node_id: 'nd_review',
  latest_epoch: 2,
  history: [
    transitionView({ from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' }),
    transitionView({ from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:02Z' }),
  ],
  usage: [STEP_USAGE],
});

describe('ChunkTimeline step usage', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it("names who ran a step — its harness alone when no runner was recorded", async () => {
    const detail: ChunkDetail = chunkDetail({
      ...COST_DETAIL,
      chunk_id: 'ch_01harness0000000000000000000',
      usage: [{ ...STEP_USAGE, harness_id: 'claude_code', harness_version: '1.2.3' }],
      graph_name: null, current_node_name: null,
    });
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', detail);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const ranBy = el.querySelector('[data-testid="history-step-harness"]');
    expect(ranBy?.textContent?.trim()).toBe('claude code');
    expect(ranBy?.getAttribute('data-harness-id')).toBe('claude_code');
    expect(ranBy?.getAttribute('title')).toBe('claude code');
  });

  it("names who ran a step as host/runner · harness, reading the runner off its spawn's usage", async () => {
    TestBed.overrideProvider(RUNNER_HOST_NAMES, { useValue: signal(new Map([[RUNNER_ID, 'xps']])) });
    TestBed.overrideProvider(RUNNER_NAMES, { useValue: signal(new Map([[RUNNER_ID, 'r-claude']])) });
    const detail: ChunkDetail = chunkDetail({
      ...COST_DETAIL,
      chunk_id: 'ch_01runner00000000000000000000',
      usage: [
        { ...STEP_USAGE, kind: 'judge', runner_id: 'rn_01other0000000000000000000' },
        { ...STEP_USAGE, kind: 'spawn', runner_id: RUNNER_ID, harness_id: 'claude_code' },
      ],
    });
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', detail);
    await fixture.whenStable();

    const ranBy = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="history-step-harness"]');
    expect(ranBy?.textContent?.trim()).toBe('xps/r-claude · claude code');
  });

  it("names a step's runner by its compact id where the app knows no name for it", async () => {
    const detail: ChunkDetail = chunkDetail({
      ...COST_DETAIL,
      chunk_id: 'ch_01runner00000000000000000000',
      usage: [{ ...STEP_USAGE, runner_id: RUNNER_ID }],
    });
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', detail);
    await fixture.whenStable();

    const ranBy = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="history-step-harness"]');
    expect(ranBy?.textContent?.trim()).toBe('R-ABF3');
  });

  it("keeps each part of a step's session efficiency cost on one line, letting the figure wrap only between parts", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // jsdom has no layout engine, so only the CSS shape guaranteeing it is assertable.
    const efficiency = el.querySelector('[data-testid="history-step-efficiency"]') as HTMLElement;
    expect(getComputedStyle(efficiency).flexWrap).toBe('wrap');
    for (const part of efficiency.querySelectorAll('.part')) {
      expect(getComputedStyle(part).whiteSpace).toBe('nowrap');
    }
  });

  it("stacks a row's stamp as its day over its time, spanning the entry's usage line", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const [withUsage, withoutUsage] = el.querySelectorAll('[data-testid="history-step"]');
    const stamp = withUsage.querySelector<HTMLElement>('[data-testid="history-when"]')!;
    expect(stamp.querySelector('.ts-time')?.textContent).toMatch(/^\d\d:\d\d$/);
    // 2026-07-13 is neither today nor yesterday against the wall clock.
    expect(stamp.querySelector('.ts-day')?.textContent).toMatch(/^\d{4}\/\d\d\/\d\d$/);
    expect(stamp.style.gridRow).toBe('1 / span 2');
    expect(withoutUsage.querySelector<HTMLElement>('[data-testid="history-when"]')!.style.gridRow).toBe('1 / span 1');
  });
});
