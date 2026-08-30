import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GraphDetailHeader } from './graph-detail-header';

describe('GraphDetailHeader', () => {
  async function mount(inputs: { graphId?: string; retired?: boolean }) {
    await TestBed.configureTestingModule({
      imports: [GraphDetailHeader],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetailHeader);
    fixture.componentRef.setInput('graphId', inputs.graphId ?? 'gr_build_v2');
    fixture.componentRef.setInput('retired', inputs.retired ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders the graph id and an enabled badge for a non-retired graph', async () => {
    const fixture = await mount({ retired: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-graph-id"]')?.textContent).toContain('gr_build_v2');
    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('enabled');
  });

  it('shows the retired badge for a retired graph', async () => {
    const fixture = await mount({ retired: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('retired');
  });
});
