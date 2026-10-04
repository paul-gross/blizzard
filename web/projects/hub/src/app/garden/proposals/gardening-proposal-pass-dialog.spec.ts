import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { GardeningProposalPassDialog } from './gardening-proposal-pass-dialog';

async function mount(route: (method: string, path: string) => unknown) {
  const stub = stubRequestClient(hubClient, route);
  await TestBed.configureTestingModule({
    imports: [GardeningProposalPassDialog],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningProposalPassDialog);
  fixture.componentRef.setInput('proposalId', 'gp_1');
  fixture.componentRef.setInput('proposalTitle', 'Author a docstring standard');
  await settle(fixture);
  return { fixture, stub, el: fixture.nativeElement as HTMLElement };
}

describe('GardeningProposalPassDialog', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('disables submit until a reason is entered', async () => {
    const mounted = await mount(() => ({}));
    stub = mounted.stub;
    const { el, fixture } = mounted;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="proposal-pass-dialog-submit"]')!.disabled).toBe(true);

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'not worth it yet';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);

    expect(el.querySelector<HTMLButtonElement>('[data-testid="proposal-pass-dialog-submit"]')!.disabled).toBe(false);
  });

  it('submits only with the entered reason, closing on success', async () => {
    const mounted = await mount(() => ({
      proposal_id: 'gp_1',
      routine_name: 'comments',
      class: 'x',
      title: 't',
      body: 'b',
      created_at: '2026-01-01T00:00:00Z',
      findings: [],
      closure: { closure: 'passed', reason: 'not worth it yet', closed_by: 'u_1', closed_at: '2026-01-02T00:00:00Z', item_outcome: null, source: null, ref: null },
    }));
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'not worth it yet';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="proposal-pass-dialog-submit"]')!.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ reason: 'not worth it yet' });
    expect(closed).toBe(true);
  });

  it('renders a 409 (already closed) as an action error rather than closing', async () => {
    const mounted = await mount((method, path) =>
      method === 'POST' && path === '/api/garden-proposals/gp_1/pass'
        ? stubError(409, { detail: 'garden proposal gp_1 already carries a closure' })
        : {},
    );
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'not worth it yet';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="proposal-pass-dialog-submit"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="proposal-pass-submit-error"]')?.textContent).toContain(
      'already carries a closure',
    );
    expect(closed).toBe(false);
  });
});
