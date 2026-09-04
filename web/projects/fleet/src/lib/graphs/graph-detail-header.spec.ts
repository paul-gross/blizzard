import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { GraphDetailHeader } from './graph-detail-header';

describe('GraphDetailHeader', () => {
  async function mount(inputs: { graphId?: string; retired?: boolean; canEdit?: boolean }) {
    await TestBed.configureTestingModule({
      imports: [GraphDetailHeader],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetailHeader);
    fixture.componentRef.setInput('graphId', inputs.graphId ?? 'gr_build_v2');
    fixture.componentRef.setInput('retired', inputs.retired ?? false);
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? true);
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

  it('renders a Retire button for a non-retired graph', async () => {
    const fixture = await mount({ retired: false });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-detail-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-detail-enable"]')).toBeNull();
  });

  it('renders an Enable button for a retired graph', async () => {
    const fixture = await mount({ retired: true });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-detail-enable"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-detail-retire"]')).toBeNull();
  });

  it('withholds the retire/enable control for a contributor (no graph:edit, #93)', async () => {
    const fixture = await mount({ retired: false, canEdit: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-retire"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-detail-enable"]')).toBeNull();
  });

  it('emits retire with the graph id once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount({ retired: false });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.retire.subscribe((graphId) => (emitted = graphId));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe('gr_build_v2');
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator cancels the retire confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = await mount({ retired: false });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.retire.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('emits enable with the graph id once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount({ retired: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.enable.subscribe((graphId) => (emitted = graphId));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe('gr_build_v2');
    confirmSpy.mockRestore();
  });
});
