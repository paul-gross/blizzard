import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GraphDiagramNodeShape } from './graph-diagram-node-shape';
import type { LaidOutNode } from './graph-layout';

async function render(executor: string): Promise<HTMLElement> {
  await TestBed.configureTestingModule({
    imports: [GraphDiagramNodeShape],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphDiagramNodeShape);
  const node = { id: 'n1', name: 'build', executor, metaLines: [], x: 0, y: 0, width: 120, height: 60 } as unknown as LaidOutNode;
  fixture.componentRef.setInput('node', node);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('GraphDiagramNodeShape executor classes', () => {
  it('marks a hub node exec-hub', async () => {
    const el = await render('hub');
    expect(el.classList.contains('exec-hub')).toBe(true);
    expect(el.classList.contains('exec-runner')).toBe(false);
  });

  it('marks a runner node exec-runner', async () => {
    const el = await render('runner');
    expect(el.classList.contains('exec-runner')).toBe(true);
    expect(el.classList.contains('exec-hub')).toBe(false);
  });

  it('gives an undeclared executor neither class and its raw badge text', async () => {
    const el = await render('edge');
    expect(el.classList.contains('exec-hub')).toBe(false);
    expect(el.classList.contains('exec-runner')).toBe(false);
    expect(el.querySelector('[data-testid="graph-diagram-node-badge"]')?.textContent?.trim()).toBe('EDGE');
  });
});
