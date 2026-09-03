import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { FleetScopePanel, type ScopePanelVm } from './scope-panel';

const VM: ScopePanelVm = {
  slug: 'blizzard',
  description: 'the blizzard monorepo',
  retired: false,
  defaultingRoutineNames: ['nightly'],
};

describe('FleetScopePanel', () => {
  async function mount(inputs: {
    vm?: ScopePanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    canEdit?: boolean;
    actionError?: string | null;
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

  it('emits retire with the slug once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.retire.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe('blizzard');
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator cancels the retire confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.retire.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('emits enable with the slug once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount({ vm: { ...VM, retired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.enable.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe('blizzard');
    confirmSpy.mockRestore();
  });

  it('lists the routines defaulting to this scope, and says so when none do', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')?.textContent).toContain('nightly');

    fixture.componentRef.setInput('vm', { ...VM, defaultingRoutineNames: [] });
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')?.textContent).toContain(
      'No routine defaults to this scope.',
    );
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
