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

import { hubUrgenciesKey } from '../../core/query-keys';
import { GardeningProposalAcceptDialog } from './gardening-proposal-accept-dialog';

const ACCEPT_RESPONSE = gardenProposalAcceptResponse({
  proposal_id: 'gp_1',
  class: 'x',
  title: 't',
  body: 'the proposal body',
});

const ROUTE = '/api/garden-proposals/gp_1/accept';

const NORMAL = {
  urgency_id: 'n',
  slug: 'normal',
  name: 'Normal',
  color: 'red',
  emoji: null,
  icon: null,
  retired: false,
  default: true,
  revision: 1,
  open_chunks: 0,
  place: 0,
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'u',
};
const URGENCIES = { urgencies: [NORMAL, { ...NORMAL, urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, place: 1 }] };

async function mount(routes: Parameters<typeof stubHubClient>[0]) {
  const stub = stubHubClient(routes);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await TestBed.configureTestingModule({
    imports: [GardeningProposalAcceptDialog],
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningProposalAcceptDialog);
  fixture.componentRef.setInput('proposalId', 'gp_1');
  fixture.componentRef.setInput('proposalTitle', 'Author a docstring standard');
  fixture.componentRef.setInput('proposalBody', 'the proposal body');
  await settle(fixture);
  const el = fixture.nativeElement as HTMLElement;
  const q = <T extends HTMLElement>(testid: string) => el.querySelector<T>(`[data-testid="${testid}"]`);
  const type = async (testid: string, value: string) => {
    const input = q<HTMLTextAreaElement>(testid)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await settle(fixture);
  };
  const choose = async (mode: 'mint' | 'decline') => {
    q(`proposal-accept-mode-${mode}`)!.click();
    await settle(fixture);
  };
  const save = async () => {
    q<HTMLButtonElement>('kit-form-save')!.click();
    await settle(fixture);
  };
  let closed = 0;
  fixture.componentInstance.closed.subscribe(() => closed++);
  return { fixture, stub, queryClient, el, q, type, choose, save, closedCount: () => closed };
}

describe('GardeningProposalAcceptDialog', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('opens in mint mode with the body prefilled, and switches between mint and decline both ways', async () => {
    const m = await mount({});
    stub = m.stub;
    expect(m.q<HTMLInputElement>('proposal-accept-mode-mint')!.checked).toBe(true);
    expect(m.q<HTMLTextAreaElement>('proposal-accept-body-input')!.value).toBe('the proposal body');

    await m.choose('decline');
    expect(m.q('proposal-accept-body-input')).toBeNull();
    expect(m.q('proposal-accept-urgency')).toBeNull();
    expect(m.q('proposal-accept-reason-input')).not.toBeNull();

    await m.choose('mint');
    expect(m.q('proposal-accept-body-input')).not.toBeNull();
    expect(m.q('proposal-accept-urgency')).not.toBeNull();
  });

  it('sends the unedited body by default and closes once the mint lands', async () => {
    const m = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = m.stub;
    await m.save();
    expect(stub.forRoute(ROUTE, 'POST').map((c) => c.body)).toEqual([
      { mint_work_item: true, body: 'the proposal body' },
    ]);
    expect(m.closedCount()).toBe(1);
  });

  it('sends a mint with every optional field emptied as the mint flag alone', async () => {
    const m = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = m.stub;
    await m.type('proposal-accept-body-input', '');
    await m.save();
    expect(stub.forRoute(ROUTE, 'POST')[0].body).toEqual({ mint_work_item: true });
  });

  it('blocks a decline with a blank reason, focusing the reason and sending nothing', async () => {
    const m = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = m.stub;
    await m.choose('decline');
    await m.type('proposal-accept-reason-input', '   ');
    await m.save();

    expect(m.q('proposal-accept-reason-field')!.textContent).toContain('Required');
    expect(document.activeElement).toBe(m.q('proposal-accept-reason-input'));
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(0);
    expect(m.closedCount()).toBe(0);
  });

  it('sends a decline with only its reason, never the edited body', async () => {
    const m = await mount({ 'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE });
    stub = m.stub;
    await m.type('proposal-accept-body-input', 'an edited body');
    await m.choose('decline');
    await m.type('proposal-accept-reason-input', ' already tracked elsewhere ');
    await m.save();
    expect(stub.forRoute(ROUTE, 'POST')[0].body).toEqual({
      mint_work_item: false,
      reason: 'already tracked elsewhere',
    });
  });

  it('keeps the tenant default by sending no urgency', async () => {
    const m = await mount({
      'GET /api/urgencies': () => URGENCIES,
      'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE,
    });
    stub = m.stub;
    await settle(m.fixture);
    expect(m.q('proposal-accept-urgency')!.textContent).toContain('Tenant default (Normal)');
    await m.save();
    expect(stub.forRoute(ROUTE, 'POST')[0].body).not.toHaveProperty('urgency');
  });

  it('sends the urgency picked in the select', async () => {
    const m = await mount({
      'GET /api/urgencies': () => URGENCIES,
      'POST /api/garden-proposals/{proposal_id}/accept': () => ACCEPT_RESPONSE,
    });
    stub = m.stub;
    await settle(m.fixture);
    m.q('proposal-accept-urgency')!.click();
    await settle(m.fixture);
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')]
      .find((option) => option.textContent?.trim() === 'Urgent')!
      .click();
    await settle(m.fixture);
    await m.save();
    expect(stub.forRoute(ROUTE, 'POST')[0].body).toMatchObject({ urgency: 'urgent' });
  });

  it('keeps an edited body through a proposalBody input change and an urgencies refetch', async () => {
    const m = await mount({ 'GET /api/urgencies': () => URGENCIES });
    stub = m.stub;
    await m.type('proposal-accept-body-input', 'my edit');

    m.fixture.componentRef.setInput('proposalBody', 'a newer proposal body');
    await settle(m.fixture);
    await m.queryClient.invalidateQueries({ queryKey: hubUrgenciesKey });
    await settle(m.fixture);

    expect(m.q<HTMLTextAreaElement>('proposal-accept-body-input')!.value).toBe('my edit');
  });

  for (const [field, testid] of [
    ['body', 'proposal-accept-body-input'],
    ['reason', 'proposal-accept-reason-input'],
    ['urgency', 'proposal-accept-urgency'],
  ] as const) {
    it(`lands a ${field} refusal on its field and focuses it`, async () => {
      const m = await mount({
        'POST /api/garden-proposals/{proposal_id}/accept': () =>
          stubError(422, { detail: [{ loc: ['body', field], msg: `${field} refused`, type: 'value_error' }] }),
      });
      stub = m.stub;
      await m.type('proposal-accept-reason-input', 'agreed');
      await m.save();

      const fieldEl = m.q(testid)!.closest('fleet-kit-field')!;
      expect(fieldEl.textContent).toContain(`${field} refused`);
      expect(m.q('kit-form-line')).toBeNull();
      expect(fieldEl.contains(document.activeElement)).toBe(true);
      expect(m.closedCount()).toBe(0);
    });
  }

  it('lands a 409 already-closed on the form line, keeping the draft and sending once', async () => {
    const m = await mount({
      'POST /api/garden-proposals/{proposal_id}/accept': () =>
        stubError(409, {
          type: 'urn:blizzard:problem:garden-proposal-already-closed',
          detail: 'garden proposal gp_1 already carries a closure',
        }),
    });
    stub = m.stub;
    await m.type('proposal-accept-body-input', 'my edit');
    await m.save();

    expect(m.q('kit-form-line')?.textContent).toContain('already carries a closure');
    expect(m.q<HTMLTextAreaElement>('proposal-accept-body-input')!.value).toBe('my edit');
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(1);
    expect(m.closedCount()).toBe(0);
  });

  it('lands a network failure on the form line, keeping the draft and sending once', async () => {
    const m = await mount({
      'POST /api/garden-proposals/{proposal_id}/accept': () => {
        throw new TypeError('Failed to fetch');
      },
    });
    stub = m.stub;
    await m.type('proposal-accept-body-input', 'my edit');
    await m.save();

    expect(m.q('kit-form-line')).not.toBeNull();
    expect(m.q<HTMLTextAreaElement>('proposal-accept-body-input')!.value).toBe('my edit');
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(1);
    expect(m.closedCount()).toBe(0);
  });

  it('sends again when Accept is pressed after a form-line refusal, clearing it once the accept lands', async () => {
    let attempts = 0;
    const m = await mount({
      'POST /api/garden-proposals/{proposal_id}/accept': () => {
        if (++attempts === 1) throw new TypeError('Failed to fetch');
        return ACCEPT_RESPONSE;
      },
    });
    stub = m.stub;
    await m.save();
    expect(m.q('kit-form-line')).not.toBeNull();
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(1);

    await m.save();
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(2);
    expect(m.q('kit-form-line')).toBeNull();
    expect(m.closedCount()).toBe(1);
  });

  it('ignores Cancel while the accept is in flight', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const m = await mount({
      'POST /api/garden-proposals/{proposal_id}/accept': async () => {
        await gate;
        return ACCEPT_RESPONSE;
      },
    });
    stub = m.stub;
    m.q<HTMLButtonElement>('kit-form-save')!.click();
    await vi.waitFor(() => expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(1));
    m.fixture.detectChanges();
    m.q<HTMLButtonElement>('kit-form-cancel')!.click();
    expect(m.closedCount()).toBe(0);
    release();
    await vi.waitFor(() => expect(m.closedCount()).toBe(1));
  });
});
