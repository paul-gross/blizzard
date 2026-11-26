import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  gardenProposalClosureView,
  gardenProposalView,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { GardeningProposalPassDialog } from './gardening-proposal-pass-dialog';

async function mount(routes: Parameters<typeof stubHubClient>[0]) {
  const stub = stubHubClient(routes);
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

  it('blocks Save on a blank reason with a field error and sends nothing', async () => {
    const mounted = await mount({});
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
    await settle(fixture);

    expect(el.textContent).toContain('Required');
    expect(stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST')).toHaveLength(0);
  });

  it('submits only with the entered reason, closing on success', async () => {
    const mounted = await mount({
      'POST /api/garden-proposals/{proposal_id}/pass': () =>
        gardenProposalView({
          proposal_id: 'gp_1',
          class: 'x',
          title: 't',
          body: 'b',
          closure: gardenProposalClosureView({ reason: 'not worth it yet' }),
        }),
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'not worth it yet';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ reason: 'not worth it yet' });
    expect(closed).toBe(true);
  });

  it('lands a hub field refusal on the reason field', async () => {
    const mounted = await mount({
      'POST /api/garden-proposals/{proposal_id}/pass': () =>
        stubError(422, {
          type: 'urn:blizzard:problem:garden-proposal-invalid',
          detail: 'reason must not be blank',
        }),
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'x';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
    await settle(fixture);

    expect(el.textContent).toContain('reason must not be blank');
    expect(el.querySelector('[data-testid="kit-form-line"]')).toBeNull();
  });

  it('renders a 409 (already closed) as a form-level refusal, keeping the draft and not retrying', async () => {
    const mounted = await mount({
      'POST /api/garden-proposals/{proposal_id}/pass': () =>
        stubError(409, {
          detail: 'garden proposal gp_1 already carries a closure',
        }),
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'not worth it yet';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="kit-form-line"]')?.textContent).toContain('already carries a closure');
    expect(closed).toBe(false);
    expect(input.value).toBe('not worth it yet');
    expect(stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST')).toHaveLength(1);
  });

  it('sends again when Pass is pressed after a form-line refusal', async () => {
    let attempts = 0;
    const mounted = await mount({
      'POST /api/garden-proposals/{proposal_id}/pass': () => {
        if (++attempts === 1) throw new TypeError('Failed to fetch');
        return gardenProposalView({ proposal_id: 'gp_1', closure: gardenProposalClosureView() });
      },
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    const input = el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
    input.value = 'not worth it yet';
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
    await settle(fixture);

    expect(stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST')).toHaveLength(2);
    expect(closed).toBe(true);
  });
});
