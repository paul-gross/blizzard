import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { FleetScopeList, type ScopeRowVm } from './scope-list';

const ROWS: readonly ScopeRowVm[] = [
  { slug: 'blizzard', description: 'the blizzard monorepo', retired: false },
  { slug: 'stale-scope', description: 'no longer tended', retired: true },
];

describe('FleetScopeList (blizzard#400)', () => {
  async function mount(inputs: { rows?: readonly ScopeRowVm[]; state?: 'loading' | 'error' | 'empty' | 'ready'; canEdit?: boolean }) {
    await TestBed.configureTestingModule({
      imports: [FleetScopeList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetScopeList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every scope with its slug, description, and retired state (AC 1, AC 5)', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const enabled = el.querySelector('[data-testid="gardening-scope-row-blizzard"]');
    expect(enabled?.textContent).toContain('blizzard');
    expect(enabled?.textContent).toContain('the blizzard monorepo');
    expect(enabled?.textContent).toContain('enabled');

    const retired = el.querySelector('[data-testid="gardening-scope-row-stale-scope"]');
    expect(retired?.textContent).toContain('retired');
  });

  it('withholds the description editor and lifecycle controls without graph:edit', async () => {
    const fixture = await mount({ canEdit: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-description-input-blizzard"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-scope-retire-blizzard"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-scope-enable-stale-scope"]')).toBeNull();
  });

  it('emits editDescription with the trimmed value on Set (AC 2)', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: { slug: string; description: string } | undefined;
    fixture.componentInstance.editDescription.subscribe((e) => (emitted = e));

    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-description-input-blizzard"]')!;
    input.value = '  updated description  ';
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-description-submit-blizzard"]')?.click();

    expect(emitted).toEqual({ slug: 'blizzard', description: 'updated description' });
  });

  it('emits retire with the slug once the operator confirms (AC 3, AC 4)', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.retire.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-retire-blizzard"]')?.click();

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

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-retire-blizzard"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('emits enable with the slug once the operator confirms (AC 3, AC 6)', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.enable.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-enable-stale-scope"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe('stale-scope');
    confirmSpy.mockRestore();
  });

  it('shows the empty state when there are no scopes', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scopes-empty"]')).toBeTruthy();
  });
});
