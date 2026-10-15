import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { HostRegistration } from './host-registration';

describe('HostRegistration', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(registration: Record<string, unknown>): Promise<{ state: string; lines: string[]; el: HTMLElement }> {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/api/host/registration' ? registration : {},
    );
    await TestBed.configureTestingModule({
      imports: [HostRegistration],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(HostRegistration);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    return {
      el,
      state: el.querySelector('[data-testid="host-registration-state"]')?.textContent?.trim() ?? '',
      lines: [...el.querySelectorAll('[data-testid="host-registration-line"]')].map((line) => line.textContent?.trim() ?? ''),
    };
  }

  it('reads unregistered, noting each runner reports on its own', async () => {
    const { state, lines } = await render({ state: 'unregistered', held_runner_ids: [], buffered_facts: 0 });

    expect(state).toBe('unregistered');
    expect(lines).toEqual(['this host holds no host token — each runner signs in and reports on its own']);
  });

  it('reads registered with its host, tenant, name, held runners, and last answer', async () => {
    const { state, lines, el } = await render({
      state: 'registered',
      host_id: 'hs_1',
      tenant_id: 'tn_1',
      host_name: 'workstation',
      answered_at: '2026-07-16T11:59:26.000Z',
      held_runner_ids: ['rn_1', 'rn_2'],
      buffered_facts: 0,
    });

    expect(state).toBe('registered');
    expect(lines).toEqual(['host hs_1', 'tenant tn_1', 'named workstation', '2 runners held']);
    expect(el.querySelector('[data-testid="host-registration-answered"]')).not.toBeNull();
  });

  it('reads refused with why', async () => {
    const { state, lines } = await render({
      state: 'refused',
      host_id: 'hs_1',
      detail: 'the hub refuses the host token (revoked)',
      held_runner_ids: [],
      buffered_facts: 0,
    });

    expect(state).toBe('refused');
    expect(lines).toEqual(['host hs_1', 'the hub refuses the host token (revoked)']);
  });

  it('reads a hub without hosts with why and the facts it buffers', async () => {
    const { state, lines } = await render({
      state: 'no_hosts',
      detail: 'the hub predates hosts',
      held_runner_ids: [],
      buffered_facts: 3,
    });

    expect(state).toBe('hub without hosts');
    expect(lines).toEqual(['the hub predates hosts', '3 facts buffered']);
  });

  it('reads pending before its first report', async () => {
    const { state, lines, el } = await render({ state: 'pending', held_runner_ids: [], buffered_facts: 0 });

    expect(state).toBe('pending');
    expect(lines).toEqual(['holds a host token, not yet reported to its hub']);
    expect(el.querySelector('[data-testid="host-registration-answered"]')).toBeNull();
  });

  it('reads unreachable with why', async () => {
    const { state, lines } = await render({
      state: 'unreachable',
      host_id: 'hs_1',
      detail: 'latest report unanswered',
      held_runner_ids: [],
      buffered_facts: 0,
    });

    expect(state).toBe('unreachable');
    expect(lines).toEqual(['host hs_1', 'latest report unanswered']);
  });
});
