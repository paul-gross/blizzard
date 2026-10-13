import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type EnvironmentRow, WorkspaceTable, type WorkspaceRow } from './workspace-table';

/** The given environments as the one heading-less pool a runner reports. */
function pool(environments: readonly EnvironmentRow[]): readonly WorkspaceRow[] {
  return [{ workspaceId: null, capacity: environments.length, inUse: 0, flag: null, environments }];
}

async function render(workspaces: readonly WorkspaceRow[]) {
  await TestBed.configureTestingModule({
    imports: [WorkspaceTable],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(WorkspaceTable);
  fixture.componentRef.setInput('workspaces', workspaces);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('WorkspaceTable', () => {
  it('renders a held row with its chunk ref and held-for text — no query stub required', async () => {
    const { el } = await render(pool([{ environmentId: 'r2', isHeld: true, chunkRef: 'C-3YJ9', heldFor: '30s', holder: null }]));

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('true');
    expect(row?.querySelector('.env')?.textContent).toBe('r2');
    expect(row?.querySelector('.chunk')?.textContent).toBe('C-3YJ9');
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('30s');
  });

  it('renders an unused row with no chunk ref and no held-for text', async () => {
    const { el } = await render(pool([{ environmentId: 'alpha', isHeld: false, chunkRef: '', heldFor: '', holder: null }]));

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('false');
    expect(row?.querySelector('.chunk')?.textContent).toBe('');
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('');
  });

  it('renders one row per given environment', async () => {
    const { el } = await render(
      pool([
        { environmentId: 'alpha', isHeld: false, chunkRef: '', heldFor: '', holder: null },
        { environmentId: 'beta', isHeld: false, chunkRef: '', heldFor: '', holder: null },
      ]),
    );

    expect(el.querySelectorAll('[data-testid="env-row"]')).toHaveLength(2);
  });

  it('renders no workspace heading for a pool reported without a workspace', async () => {
    const { el } = await render(pool([{ environmentId: 'alpha', isHeld: false, chunkRef: '', heldFor: '', holder: null }]));

    expect(el.querySelector('[data-testid="workspace-row"]')).toBeNull();
  });

  it('heads each workspace with its id, in-use of capacity, and flag', async () => {
    const { el } = await render([
      { workspaceId: 'ws-1', capacity: 3, inUse: 2, flag: null, environments: [] },
      { workspaceId: 'ws-2', capacity: null, inUse: 0, flag: 'UNREPORTED', environments: [] },
    ]);

    const rows = el.querySelectorAll('[data-testid="workspace-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-workspace-id')).toBe('ws-1');
    expect(rows[0].querySelector('[data-testid="workspace-in-use"]')?.textContent?.trim()).toBe('2 / 3 in use');
    expect(rows[0].querySelector('[data-testid="workspace-flag"]')).toBeNull();
    expect(rows[1].querySelector('[data-testid="workspace-in-use"]')?.textContent?.trim()).toBe('0 in use');
    expect(rows[1].querySelector('[data-testid="workspace-flag"]')?.textContent).toBe('UNREPORTED');
  });

  it('names an environment\'s holder only when the row carries one', async () => {
    const { el } = await render(
      pool([
        { environmentId: 'e1', isHeld: true, chunkRef: 'C-3YJ9', heldFor: '1m', holder: 'rA' },
        { environmentId: 'e2', isHeld: false, chunkRef: '', heldFor: '', holder: null },
      ]),
    );

    const [held, free] = Array.from(el.querySelectorAll('[data-testid="env-row"]'));
    expect(held.querySelector('[data-testid="env-holder"]')?.textContent).toBe('rA');
    expect(free.querySelector('[data-testid="env-holder"]')).toBeNull();
  });
});
