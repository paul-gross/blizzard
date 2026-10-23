import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { RunnerConnection, type WorkspaceRow } from 'fleet';
import { type HostDetailVm, resolveSelection } from './fleet-page.model';
import { HostDetail } from './host-detail';
import type { HostCard } from './host-rows.model';
import type { RunnerRow } from './runner-rows.model';

function card(over: Partial<HostCard> = {}): HostCard {
  return {
    hostId: 'hs_AAAA1111',
    hostName: 'atlas',
    online: true,
    connection: RunnerConnection.ONLINE,
    reportLabel: 'seen 4s ago',
    workspaces: [],
    subscriptionPaces: [],
    url: 'https://atlas.example',
    enrolled: true,
    registeredAt: '2026-01-02T03:04:05Z',
    addedAt: '2026-01-01T00:00:00Z',
    addedBy: 'paul',
    lastSeenLabel: 'Jul 16 11:59',
    reportedAgo: '4s ago',
    heldChunks: new Map(),
    ...over,
  };
}

function row(id: string, agents: number, agentsInUse: number): RunnerRow {
  return {
    runner_id: id,
    runner_name: `name-${id}`,
    online: true,
    connection: RunnerConnection.ONLINE,
    claims: [],
    served: [],
    unresolved: [],
    unassigned: false,
    subscriptionPaces: [],
    subscriptionName: null,
    bindingLines: [{ project: 'p', workspaceId: 'ws_a', unreported: false, agents, agentsInUse }],
  } as unknown as RunnerRow;
}

function vm(c: HostCard, rows: readonly RunnerRow[] = []): HostDetailVm {
  const sel = resolveSelection({ cards: [{ card: c, rows }], hostless: [] }, c.hostId);
  if (sel?.kind !== 'host') throw new Error('expected a host');
  return sel;
}

const ws = (id: string, capacity: number | null, inUse: number): WorkspaceRow => ({
  workspaceId: id,
  capacity,
  inUse,
  flag: null,
  environments: [],
});

const text = (el: HTMLElement, testid: string) =>
  el.querySelector(`[data-testid="${testid}"]`)?.textContent?.replace(/\s+/g, ' ').trim();

describe('HostDetail', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HostDetail],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  async function render(detail: HostDetailVm): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(HostDetail);
    fixture.componentRef.setInput('detail', detail);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('titles a live host with its state and the age of its last report', async () => {
    const el = await render(vm(card()));

    expect(text(el, 'fleet-detail-state')).toBe('ONLINE');
    expect(text(el, 'fleet-detail-report')).toBe('reported 4s ago');
  });

  it('says never reported only for a never-connected host, and states no age it cannot read', async () => {
    const never = await render(vm(card({ online: false, connection: RunnerConnection.NEVER_CONNECTED, reportedAgo: null })));
    expect(text(never, 'fleet-detail-state')).toBe('NEVER CONNECTED');
    expect(text(never, 'fleet-detail-report')).toBe('never reported');

    const skewed = await render(vm(card({ reportedAgo: null })));
    expect(text(skewed, 'fleet-detail-state')).toBe('ONLINE');
    expect(skewed.querySelector('[data-testid="fleet-detail-report"]')).toBeNull();
    expect(text(skewed, 'fleet-fact-last-report')).toBe('Jul 16 11:59');
  });

  it('states the connection: last report with its age, URL, token enrollment, and agents busy across its runners', async () => {
    const el = await render(vm(card(), [row('rn_1', 2, 1), row('rn_2', 3, 2)]));

    expect(text(el, 'fleet-fact-last-report')).toBe('Jul 16 11:59 4s ago');
    expect(text(el, 'fleet-fact-url')).toBe('https://atlas.example');
    expect(text(el, 'fleet-fact-token')).toBe('ENROLLED');
    expect(text(el, 'fleet-fact-agents')).toBe('3 / 5 busy across 2 runners');

    const unenrolled = await render(vm(card({ enrolled: false })));
    expect(text(unenrolled, 'fleet-fact-token')).toBe('UNENROLLED');
  });

  it('counts environments in use over capacity, and says when a section has nothing reported', async () => {
    const el = await render(vm(card({ workspaces: [ws('ws_a', 3, 1), ws('ws_b', 2, 1)] })));
    expect(text(el, 'fleet-workspaces')).toContain('2 / 5 in use');
    expect(el.querySelectorAll('[data-testid="fleet-env-free"]')).toHaveLength(3);

    const bare = await render(vm(card()));
    expect(text(bare, 'fleet-workspaces-empty')).toBe('NO WORKSPACES REPORTED');
    expect(text(bare, 'fleet-subscriptions-empty')).toBe('NO SUBSCRIPTIONS REPORTED');
    expect(text(bare, 'fleet-runners-empty')).toBe('NO RUNNERS UNDER THIS HOST');
  });
});
