import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GraphDetailLifecycle } from './graph-detail-lifecycle';

describe('GraphDetailLifecycle', () => {
  async function mount(inputs: { actionError?: string | null; entryNodeName?: string }) {
    await TestBed.configureTestingModule({
      imports: [GraphDetailLifecycle],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetailLifecycle);
    fixture.componentRef.setInput('actionError', inputs.actionError ?? null);
    fixture.componentRef.setInput('entryNodeName', inputs.entryNodeName ?? 'build');
    await fixture.whenStable();
    return fixture;
  }

  it('renders the entry-node line', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-entry"]')?.textContent).toContain('build');
  });

  it('shows the action-error line only when one is set (issue #42)', async () => {
    const fixture = await mount({ actionError: null });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-detail-lifecycle-error"]')).toBeNull();

    fixture.componentRef.setInput('actionError', 'already retired somehow');
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-error"]')?.textContent).toContain(
      'already retired somehow',
    );
  });
});
