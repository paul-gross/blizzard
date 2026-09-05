import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type EnvRow, EnvListView } from './env-list-view';

async function render(rows: readonly EnvRow[]) {
  await TestBed.configureTestingModule({
    imports: [EnvListView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(EnvListView);
  fixture.componentRef.setInput('rows', rows);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('EnvListView', () => {
  it('renders a held row with its chunk ref and held-for text — no query stub required', async () => {
    const { el } = await render([{ environmentId: 'r2', isHeld: true, chunkRef: 'C-3YJ9', heldFor: '30s' }]);

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('true');
    expect(row?.querySelector('.env')?.textContent).toBe('r2');
    expect(row?.querySelector('.chunk')?.textContent).toBe('C-3YJ9');
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('30s');
  });

  it('renders an unused row with no chunk ref and no held-for text', async () => {
    const { el } = await render([{ environmentId: 'alpha', isHeld: false, chunkRef: '', heldFor: '' }]);

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('false');
    expect(row?.querySelector('.chunk')?.textContent).toBe('');
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('');
  });

  it('renders one row per given environment', async () => {
    const { el } = await render([
      { environmentId: 'alpha', isHeld: false, chunkRef: '', heldFor: '' },
      { environmentId: 'beta', isHeld: false, chunkRef: '', heldFor: '' },
    ]);

    expect(el.querySelectorAll('[data-testid="env-row"]')).toHaveLength(2);
  });
});
