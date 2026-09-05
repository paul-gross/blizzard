import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';

import { MachineDetailView } from './chunk-detail-view';

const LEASE: runnerApi.LeaseView = {
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNNEW1',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-new',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  closed_at: null,
  closure_reason: null,
};

async function render(overrides: Partial<{ lease: runnerApi.LeaseView | null; escalation: runnerApi.EscalationView | null }> = {}) {
  await TestBed.configureTestingModule({
    imports: [MachineDetailView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetailView);
  if (overrides.lease !== undefined) fixture.componentRef.setInput('lease', overrides.lease);
  if (overrides.escalation !== undefined) fixture.componentRef.setInput('escalation', overrides.escalation);
  fixture.componentRef.setInput('leaseRef', 'L-EW1');
  fixture.componentRef.setInput('heartbeatLabel', '-34s');
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('MachineDetailView', () => {
  it('shows SELECT A CHUNK when no lease is given — no query stub required', async () => {
    const { el } = await render({ lease: null });

    expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
  });

  it('shows the execution facts for the given lease', async () => {
    const { el } = await render({ lease: LEASE });

    const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
    expect(facts).toContain(LEASE.lease_id);
    expect(facts).toContain('sess-new');
    expect(el.querySelector('[data-testid="heartbeat-label"]')?.textContent).toContain('-34s');
  });

  it('renders no resume box when there is no open escalation', async () => {
    const { el } = await render({ lease: LEASE, escalation: null });

    expect(el.querySelector('[data-testid="detail-resume"]')).toBeNull();
  });

  it('renders the resume command when an escalation is open', async () => {
    const { el } = await render({
      lease: LEASE,
      escalation: {
        chunk_id: LEASE.chunk_id,
        lease_id: LEASE.lease_id,
        node_id: LEASE.node_id,
        epoch: LEASE.epoch,
        closed_at: '2026-07-16T11:00:00.000Z',
        resume_command: 'blizzard runner resume sess-new',
      },
    });

    expect(el.querySelector('[data-testid="detail-resume"]')?.textContent).toContain('blizzard runner resume sess-new');
  });
});
