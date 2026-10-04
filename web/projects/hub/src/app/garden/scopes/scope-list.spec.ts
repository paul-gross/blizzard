import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetScopeList, type ScopeRowVm } from './scope-list';

const ROWS: readonly ScopeRowVm[] = [
  { slug: 'blizzard', description: 'the blizzard monorepo', retired: false },
  { slug: 'stale-scope', description: 'no longer tended', retired: true },
];

describe('FleetScopeList', () => {
  async function mount(inputs: {
    rows?: readonly ScopeRowVm[];
    state?: 'loading' | 'error' | 'empty' | 'ready';
    selectedSlug?: string | null;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetScopeList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetScopeList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('selectedSlug', inputs.selectedSlug ?? null);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every scope with its slug, and a retired marker only on a retired scope', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const enabled = el.querySelector('[data-testid="gardening-scope-row-blizzard"]');
    expect(enabled?.textContent).toContain('blizzard');
    expect(enabled?.textContent).not.toContain('retired');

    const retired = el.querySelector('[data-testid="gardening-scope-row-stale-scope"]');
    expect(retired?.textContent).toContain('retired');
  });

  it('reflects selectedSlug onto the matching row only', async () => {
    const fixture = await mount({ selectedSlug: 'blizzard' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-row-blizzard"]')?.classList.contains('selected')).toBe(
      true,
    );
    expect(
      el.querySelector('[data-testid="gardening-scope-row-stale-scope"]')?.classList.contains('selected'),
    ).toBe(false);
  });

  it('emits scopePick with the slug on a row click', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.scopePick.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-row-blizzard"]')?.click();

    expect(emitted).toBe('blizzard');
  });

  it('shows the empty state when there are no scopes', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scopes-empty"]')).toBeTruthy();
  });
});
