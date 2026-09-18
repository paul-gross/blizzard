import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GraphDetailHeader } from './graph-detail-header';

describe('GraphDetailHeader', () => {
  async function mount(inputs: {
    graphId?: string;
    retired?: boolean;
    canEdit?: boolean;
    lifecyclePending?: boolean;
    /** The container's already-applied result — defaults to the real `retired` when
     * omitted, since a caller not exercising the override cares about the plain
     * real-status render. */
    renderedRetired?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [GraphDetailHeader],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetailHeader);
    fixture.componentRef.setInput('graphId', inputs.graphId ?? 'gr_build_v2');
    fixture.componentRef.setInput('retired', inputs.retired ?? false);
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? true);
    fixture.componentRef.setInput('lifecyclePending', inputs.lifecyclePending ?? false);
    fixture.componentRef.setInput('renderedRetired', inputs.renderedRetired ?? inputs.retired ?? false);
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

  it('disables Retire while the lifecycle mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ retired: false, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('lifecyclePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.disabled).toBe(false);
  });

  it('disables Enable while the lifecycle mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ retired: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('lifecyclePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.disabled).toBe(false);
  });

  it('emits retire with the graph id once the operator confirms', async () => {
    const fixture = await mount({ retired: false });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.retire.subscribe((graphId) => (emitted = graphId));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('gr_build_v2');
  });

  it('emits nothing when the operator cancels the retire confirm', async () => {
    const fixture = await mount({ retired: false });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.retire.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('emits enable with the graph id once the operator confirms', async () => {
    const fixture = await mount({ retired: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.enable.subscribe((graphId) => (emitted = graphId));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('gr_build_v2');
  });

  // --- Pending lifecycle override (`bzh:frontend-pending-override`) ----------------
  //
  // The merge itself (`renderedRetired` vs. the real `retired`) is the container's,
  // not this presentational component's — this header only renders whatever the
  // container already resolved (`graph-detail.ts`'s `overrideRetired`/`renderedRetired`
  // own that merge and its own coverage). What this header still owns: the badge
  // tracks `renderedRetired` even where it disagrees with the real `retired`, and the
  // control choice stays keyed off the real `retired` regardless.

  it('renders the retired badge from renderedRetired, even though the real retired is false', async () => {
    const fixture = await mount({ retired: false, renderedRetired: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('retired');
  });

  it('renders the enabled badge from renderedRetired, even though the real retired is true', async () => {
    const fixture = await mount({ retired: true, renderedRetired: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('enabled');
  });

  it('keeps the control keyed off the real retired, not renderedRetired — a pending retire still shows Retire, not Enable', async () => {
    const fixture = await mount({ retired: false, renderedRetired: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-detail-enable"]')).toBeNull();
  });
});
