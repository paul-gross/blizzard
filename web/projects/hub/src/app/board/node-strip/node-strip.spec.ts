import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { settle } from 'fleet/testing';
import { NodeStrip } from './node-strip';
import type { NodeBlock } from './node-strip.model';

/** `n` nodes, each in the state `states` names at its index, else unvisited. */
const progress = (n: number, states: readonly NodeBlock['state'][] = []): NodeBlock[] =>
  Array.from({ length: n }, (_, i) => ({ node_id: `nd_n${i}`, name: `n${i}`, state: states[i] ?? 'unvisited' }));

describe('NodeStrip', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [NodeStrip], providers: [provideZonelessChangeDetection()] }));

  async function render(blocks: readonly NodeBlock[]) {
    const fixture = TestBed.createComponent(NodeStrip);
    fixture.componentRef.setInput('progress', blocks);
    await settle(fixture);
    return fixture.nativeElement as HTMLElement;
  }

  const blocks = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.block')];

  it('draws a block per node in the order handed in, each in its state', async () => {
    const el = await render(progress(4, ['current', 'failed']));

    expect(el.querySelector('[data-testid="node-strip"]')?.getAttribute('aria-label')).toBe('Graph progress');
    expect(blocks(el).map((block) => block.getAttribute('data-state'))).toEqual(['current', 'failed', 'unvisited', 'unvisited']);
    expect(blocks(el)[1].title).toBe('n1 · failed');
  });

  it('breaks the strip into groups of ten, packing it dense past twenty', async () => {
    const el = await render(progress(25));

    expect([...el.querySelectorAll('.group')].map((group) => group.querySelectorAll('.block').length)).toEqual([10, 10, 5]);
    expect(el.classList).toContain('dense');
  });

  it('stays loose at twenty blocks', async () => {
    const el = await render(progress(20));

    expect(el.querySelectorAll('.group')).toHaveLength(2);
    expect(el.classList).not.toContain('dense');
  });

  it('draws nothing for a chunk with no progress', async () => {
    expect((await render([])).querySelector('[data-testid="node-strip"]')).toBeNull();
  });
});
