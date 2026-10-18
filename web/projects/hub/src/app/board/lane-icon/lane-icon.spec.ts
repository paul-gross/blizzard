import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type ChunkStatus, STATUS_TONE, toneColor } from 'fleet';
import { LaneIcon, laneIconGlyph } from './lane-icon';

describe('laneIconGlyph', () => {
  it("draws the lane, but for the statuses that read apart from their lane's", () => {
    expect(laneIconGlyph('running', 'delivering')).toBe('running');
    expect(laneIconGlyph('waiting', 'waiting_on_human')).toBe('waiting');
    expect(laneIconGlyph('waiting', 'paused')).toBe('paused');
    expect(laneIconGlyph('done', 'done')).toBe('done');
    expect(laneIconGlyph('done', 'stopped')).toBe('stopped');
  });

  it('falls back to the inert backlog drawing for a lane it has no drawing for', () => {
    expect(laneIconGlyph('elsewhere', 'ready')).toBe('notready');
  });
});

describe('LaneIcon', () => {
  async function render(lane: string, status: ChunkStatus) {
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
    const el = await render('needs', 'needs_human');
    expect(el.getAttribute('data-glyph')).toBe('needs');
    expect(el.getAttribute('title')).toBe('Needs human');
    expect(el.querySelector('svg')?.getAttribute('aria-label')).toBe('Needs human');
  });

  it("colors itself by the status's shared tone", async () => {
    const el = await render('needs', 'needs_human');
    expect(el.style.color).toBe(toneColor(STATUS_TONE.needs_human));
  });

  it('names a status-picked drawing by the status', async () => {
    const el = await render('done', 'stopped');
    expect(el.getAttribute('title')).toBe('Stopped');
  });
});
