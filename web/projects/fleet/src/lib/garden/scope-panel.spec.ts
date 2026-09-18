import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetScopePanel, type ScopePanelVm } from './scope-panel';

const VM: ScopePanelVm = {
  slug: 'blizzard',
  description: 'the blizzard monorepo',
  retired: false,
  relatedRoutines: [{ name: 'nightly', isDefault: true }],
};

describe('FleetScopePanel', () => {
  async function mount(inputs: {
    vm?: ScopePanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    canEdit?: boolean;
    actionError?: string | null;
    editPending?: boolean;
    lifecyclePending?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetScopePanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetScopePanel);
    fixture.componentRef.setInput('vm', inputs.vm === undefined ? VM : inputs.vm);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? false);
    fixture.componentRef.setInput('actionError', inputs.actionError ?? null);
    fixture.componentRef.setInput('editPending', inputs.editPending ?? false);
    fixture.componentRef.setInput('lifecyclePending', inputs.lifecyclePending ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders the slug, its enabled state, and the description as plain text without graph:edit', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-scope-panel"]');
    expect(panel?.textContent).toContain('blizzard');
    expect(el.querySelector('[data-testid="gardening-scope-panel-state"]')?.textContent).toContain('enabled');
    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeNull();
    expect(panel?.textContent).toContain('the blizzard monorepo');
  });

  it('marks a retired scope distinctly', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-state"]')?.textContent).toContain('retired');
  });

  it('shows the description editor and retire control for an identity with graph:edit', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-enable"]')).toBeNull();
  });

  it('shows Re-enable instead of Retire once retired', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-enable"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeNull();
  });

  it('emits editDescription with the trimmed value on Set', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: { slug: string; description: string } | undefined;
    fixture.componentInstance.editDescription.subscribe((e) => (emitted = e));

    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-panel-description-input"]')!;
    input.value = '  updated description  ';
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.click();

    expect(emitted).toEqual({ slug: 'blizzard', description: 'updated description' });
  });

  it('disables Set while the edit mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ canEdit: true, editPending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.disabled).toBe(
      true,
    );

    fixture.componentRef.setInput('editPending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.disabled).toBe(
      false,
    );
  });

  it('disables Retire while the lifecycle mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ canEdit: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('lifecyclePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.disabled).toBe(false);
  });

  it('disables Re-enable while the lifecycle mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true }, canEdit: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('lifecyclePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.disabled).toBe(false);
  });

  it('emits retire with the slug once the operator confirms', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.retire.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('blizzard');
  });

  it('emits nothing when the operator cancels the retire confirm', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.retire.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('emits enable with the slug once the operator confirms', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.enable.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('blizzard');
  });

  it('lists the routines related to this scope, marking the one that defaults here', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-scope-panel-routines"]');
    expect(section?.textContent).toContain('nightly');
    expect(section?.querySelector('[data-testid="gardening-scope-panel-routine-default"]')).toBeTruthy();
  });

  it('says so when no routine is related, and omits the default marker for a non-defaulting one', async () => {
    const fixture = await mount({
      vm: { ...VM, relatedRoutines: [{ name: 'other', isDefault: false }] },
    });
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-scope-panel-routines"]');
    expect(section?.textContent).toContain('other');
    expect(section?.querySelector('[data-testid="gardening-scope-panel-routine-default"]')).toBeNull();

    fixture.componentRef.setInput('vm', { ...VM, relatedRoutines: [] });
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')?.textContent).toContain(
      'No routine is related to this scope.',
    );
  });

  it('renders no related-routines section while the relation read is still pending', async () => {
    const fixture = await mount({ vm: { ...VM, relatedRoutines: null } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')).toBeNull();
  });

  it('renders the action error beside the controls that raise it', async () => {
    const fixture = await mount({ canEdit: true, actionError: 'Retire failed.' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-error"]')?.textContent).toBe('Retire failed.');
  });

  it('renders Retire at the cta size', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    const retire = el.querySelector('[data-testid="gardening-scope-panel-retire"]');
    expect(retire?.classList.contains('cta')).toBe(true);
  });

  it('renders Re-enable at the cta size', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    const enable = el.querySelector('[data-testid="gardening-scope-panel-enable"]');
    expect(enable?.classList.contains('cta')).toBe(true);
  });

  it('shows the empty state when nothing is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-empty"]')).toBeTruthy();
  });
});
