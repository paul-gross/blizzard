import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { UNKNOWN_LANE, STATUS_TONE, type LaneStatus } from '../../core/chunk-lanes';
import { toneColor } from '../../kit/kit-badge';
import { LaneIcon, laneIconGlyph } from './lane-icon';

describe('laneIconGlyph', () => {
  it("draws the lane, but for the statuses that read apart from their lane's", () => {
    expect(laneIconGlyph('running', { kind: 'delivering' })).toBe('running');
    expect(laneIconGlyph('waiting', { kind: 'waiting_on_human' })).toBe('waiting');
    expect(laneIconGlyph('waiting', { kind: 'paused' })).toBe('paused');
    expect(laneIconGlyph('done', { kind: 'done' })).toBe('done');
    expect(laneIconGlyph('done', { kind: 'stopped' })).toBe('stopped');
  });

  it('falls back to the inert backlog drawing for a lane it has no drawing for', () => {
    expect(laneIconGlyph('elsewhere', { kind: 'ready' })).toBe('notready');
  });
});

describe('LaneIcon', () => {
  async function render(lane: string, status: LaneStatus) {
    await TestBed.configureTestingModule({
      imports: [LaneIcon],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(LaneIcon);
    fixture.componentRef.setInput('lane', lane);
    fixture.componentRef.setInput('status', status);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it("names the lane by its heading, as both tooltip and accessible name", async () => {
    const el = await render('needs', { kind: 'needs_human' });
    expect(el.getAttribute('data-glyph')).toBe('needs');
    expect(el.getAttribute('title')).toBe('Needs human');
    expect(el.querySelector('svg')?.getAttribute('aria-label')).toBe('Needs human');
  });

  it("colors itself by the status's shared tone", async () => {
    const el = await render('needs', { kind: 'needs_human' });
    expect(el.style.color).toBe(toneColor(STATUS_TONE('needs_human')));
  });

  it('names a status-picked drawing by the status', async () => {
    const el = await render('done', { kind: 'stopped' });
    expect(el.getAttribute('title')).toBe('Stopped');
  });

  it('names a status this build does not know by its raw value, in the inert idle tone', async () => {
    const el = await render(UNKNOWN_LANE, { kind: 'unknown', raw: 'newer-status' });
    expect(el.getAttribute('data-glyph')).toBe('notready');
    expect(el.getAttribute('title')).toBe('newer-status');
    expect(el.style.color).toBe(toneColor('idle'));
  });
});
