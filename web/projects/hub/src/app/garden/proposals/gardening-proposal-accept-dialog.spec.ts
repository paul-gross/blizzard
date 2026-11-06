import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  gardenProposalAcceptResponse,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { GardeningProposalAcceptDialog } from './gardening-proposal-accept-dialog';

const ACCEPT_RESPONSE = gardenProposalAcceptResponse({
  proposal_id: 'gp_1',
  class: 'x',
  title: 't',
  body: 'the proposal body',
});

async function mount(routes: Parameters<typeof stubHubClient>[0]) {
  const stub = stubHubClient(routes);
  await TestBed.configureTestingModule({
    imports: [GardeningProposalAcceptDialog],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningProposalAcceptDialog);
  fixture.componentRef.setInput('proposalId', 'gp_1');
  fixture.componentRef.setInput('proposalTitle', 'Author a docstring standard');
  fixture.componentRef.setInput('proposalBody', 'the proposal body');
  await settle(fixture);
  return { fixture, stub, el: fixture.nativeElement as HTMLElement };
}

describe('GardeningProposalAcceptDialog', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('submits mint_work_item: true with the (unedited) proposal body and no extra input by default', async () => {
    const mounted = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.disabled).toBe(
      false,
    );
    el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/garden-proposals/gp_1/accept', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ mint_work_item: true, body: 'the proposal body' });
  });

  it('carries an edited body override', async () => {
    const mounted = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    const bodyInput = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-accept-body-input"]')!;
    bodyInput.value = 'a different body';
    bodyInput.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/garden-proposals/gp_1/accept', 'POST');
    expect(calls[0].body).toEqual({ mint_work_item: true, body: 'a different body' });
  });

  it('gates decline-to-mint on its own required reason, then sends mint_work_item: false', async () => {
    const mounted = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="proposal-accept-mode-decline"]')!.click();
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.disabled).toBe(
      true,
    );

    const reasonInput = el.querySelector<HTMLTextAreaElement>(
      '[data-testid="proposal-accept-decline-reason-input"]',
    )!;
    reasonInput.value = 'already tracked elsewhere';
    reasonInput.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.disabled).toBe(
      false,
    );

    el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/garden-proposals/gp_1/accept', 'POST');
    expect(calls[0].body).toEqual({ mint_work_item: false, reason: 'already tracked elsewhere' });
  });

  it('renders a 409 (already closed) as an action error rather than closing', async () => {
    const mounted = await mount({
      'POST /api/garden-proposals/{proposal_id}/accept': () => stubError(409, { detail: 'garden proposal gp_1 already carries a closure' }),
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    el.querySelector<HTMLButtonElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="proposal-accept-submit-error"]')?.textContent).toContain(
      'already carries a closure',
    );
    expect(closed).toBe(false);
  });
});
