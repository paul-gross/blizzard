import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import type { TranscriptSegmentIndexEntry } from '../api/hub';
import type { TranscriptTurn } from './transcript-turn';
import { TranscriptSegmentView } from './transcript-segment-view';

function turn(index: number, text = `turn ${index}`): TranscriptTurn {
  return {
    index,
    kind: 'asst',
    timestamp: null,
    text,
    tool: null,
    thinking_redacted: false,
    sidechain: null,
    truncated: false,
  };
}

function seam(overrides: Partial<TranscriptSegmentIndexEntry> = {}): TranscriptSegmentIndexEntry {
  return {
    segment_id: 'sg_prev',
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: 0,
    turn_range_start: 0,
    turn_range_end: 10,
    final: true,
    truncated: false,
    byte_count: 100,
    normalizer_version: 'v1',
    harness_version: null,
    received_at: '2026-08-09T00:00:00+00:00',
    ...overrides,
  };
}

interface RenderOptions {
  turns?: readonly TranscriptTurn[];
  truncated?: boolean;
  continuedFrom?: TranscriptSegmentIndexEntry | null;
  continuesIn?: TranscriptSegmentIndexEntry | null;
}

async function render(options: RenderOptions = {}): Promise<{ el: HTMLElement; fixture: ComponentFixture<TranscriptSegmentView> }> {
  await TestBed.configureTestingModule({
    imports: [TranscriptSegmentView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(TranscriptSegmentView);
  fixture.componentRef.setInput('turns', options.turns ?? [turn(0, 'hello')]);
  fixture.componentRef.setInput('truncated', options.truncated ?? false);
  fixture.componentRef.setInput('continuedFrom', options.continuedFrom ?? null);
  fixture.componentRef.setInput('continuesIn', options.continuesIn ?? null);
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

describe('TranscriptSegmentView', () => {
  it('renders the given turns via TranscriptViewer with no seams or banners by default', async () => {
    const { el } = await render();

    expect(el.textContent).toContain('hello');
    expect(el.querySelector('[data-testid="transcript-continued-from"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-continues-in"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-segment-truncated"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-segment-turns-capped"]')).toBeNull();
  });

  it('renders a truncated banner when truncated is set', async () => {
    const { el } = await render({ truncated: true });

    expect(el.querySelector('[data-testid="transcript-segment-truncated"]')?.textContent).toContain('TRUNCATED');
  });

  it('caps a large turn list at 1000 and says so', async () => {
    const turns = Array.from({ length: 1200 }, (_, i) => turn(i));
    const { el } = await render({ turns });

    expect(el.querySelector('[data-testid="transcript-segment-turns-capped"]')?.textContent).toContain('1000');
    expect(el.querySelectorAll('[data-testid="transcript-turn"]')).toHaveLength(1000);
    // The cap keeps the most recent turns, not the earliest.
    expect(el.textContent).toContain('turn 1199');
    expect(el.textContent).not.toContain('turn 0 ');
  });

  it('renders continued-from/continues-in seams and emits pickSegment when followed', async () => {
    const prev = seam({ segment_id: 'sg_prev', spawn_generation: 0 });
    const next = seam({ segment_id: 'sg_next', spawn_generation: 2 });
    const { el, fixture } = await render({ continuedFrom: prev, continuesIn: next });
    const emitted: string[] = [];
    fixture.componentInstance.pickSegment.subscribe((id) => emitted.push(id));

    const back = el.querySelector<HTMLButtonElement>('[data-testid="transcript-continued-from"]');
    expect(back?.textContent).toContain('segment 1');
    back?.click();

    const forward = el.querySelector<HTMLButtonElement>('[data-testid="transcript-continues-in"]');
    expect(forward?.textContent).toContain('segment 3');
    forward?.click();

    expect(emitted).toEqual(['sg_prev', 'sg_next']);
  });
});
