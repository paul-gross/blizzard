import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkDetailSkeleton } from './chunk-detail-skeleton';

describe('ChunkDetailSkeleton', () => {
  async function render(): Promise<HTMLElement> {
    await TestBed.configureTestingModule({ imports: [ChunkDetailSkeleton], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(ChunkDetailSkeleton);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('announces itself as a busy region while the detail loads', async () => {
    const el = await render();

    expect(el.getAttribute('role')).toBe('region');
    expect(el.getAttribute('aria-busy')).toBe('true');
    expect(el.getAttribute('aria-label')).toBe('Loading chunk detail');
  });

  it('holds the dock\'s shape: a header with its controls, then the three columns', async () => {
    const el = await render();

    expect(el.querySelectorAll('.head .title fleet-kit-skeleton')).toHaveLength(2);
    expect(el.querySelectorAll('.head .controls fleet-kit-skeleton')).toHaveLength(2);
    const columns = [...el.querySelectorAll('.body > .col')];
    expect(columns).toHaveLength(3);
    // The work-item column: the Work item box with its two dependency sides (2 + 3 edges), then the issue rows.
    expect(columns[0].classList).toContain('col--work');
    expect(columns[0].querySelectorAll(':scope > .box')).toHaveLength(2);
    expect(columns[0].querySelectorAll('.edges > .edge-col')).toHaveLength(2);
    expect(columns[0].querySelectorAll('.edge')).toHaveLength(5);
    expect(columns[0].querySelectorAll(':scope > .acc')).toHaveLength(3);
    // The node-history and artifacts columns: a heading each over their rows.
    expect(columns.slice(1).map((col) => col.querySelector(':scope > .s-head') !== null)).toEqual([true, true]);
    expect(columns[1].querySelectorAll('.timeline > .step')).toHaveLength(6);
    expect(columns[1].querySelectorAll('.step')[0].querySelectorAll('.cell')).toHaveLength(3);
    expect(columns[2].querySelectorAll(':scope > .art')).toHaveLength(5);
  });

  it('carries no text, only bars', async () => {
    expect((await render()).textContent?.trim()).toBe('');
  });
});
