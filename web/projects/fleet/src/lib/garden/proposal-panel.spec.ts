import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetProposalPanel, type ProposalClosureVm, type ProposalEvidenceRowVm, type ProposalPanelVm } from './proposal-panel';

const BASE_VM: ProposalPanelVm = {
  proposalId: 'gp_1',
  routineName: 'comments',
  proposalClass: 'fix-the-source',
  title: 'Author a docstring standard',
  body: 'Seventeen modules narrate their own change history.',
  closure: null,
};

const EVIDENCE: readonly ProposalEvidenceRowVm[] = [
  { findingId: 'fin_1', locus: 'src/a.py:1', summary: 'stale docstring', live: true, workItem: null },
  { findingId: 'fin_2', locus: 'src/b.py:9', summary: 'another one', live: false, workItem: null },
];

describe('FleetProposalPanel', () => {
  async function mount(inputs: {
    vm?: ProposalPanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    evidence?: readonly ProposalEvidenceRowVm[];
    evidenceState?: 'loading' | 'error' | 'empty' | 'ready';
    canControl?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetProposalPanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetProposalPanel);
    fixture.componentRef.setInput('vm', inputs.vm ?? BASE_VM);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('evidence', inputs.evidence ?? EVIDENCE);
    fixture.componentRef.setInput('evidenceState', inputs.evidenceState ?? 'ready');
    fixture.componentRef.setInput('canControl', inputs.canControl ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders the select-a-proposal rest state when nothing is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-panel-empty"]')?.textContent).toContain(
      'Select a proposal',
    );
  });

  it('renders the case as prose', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const caseEl = el.querySelector('[data-testid="gardening-proposal-case"]');
    expect(caseEl?.textContent).toContain('Author a docstring standard');
    expect(caseEl?.textContent).toContain('Seventeen modules narrate their own change history.');
    expect(caseEl?.textContent).toContain('fix-the-source');
  });

  it('renders findings from the live evidence read, not from any proposal-carried text', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const live = el.querySelector('[data-testid="gardening-proposal-finding-fin_1"]');
    expect(live?.textContent).toContain('src/a.py:1');
    expect(live?.textContent).toContain('stale docstring');
    expect(live?.querySelector('[data-testid="gardening-proposal-finding-not-live"]')).toBeNull();

    const gone = el.querySelector('[data-testid="gardening-proposal-finding-fin_2"]');
    expect(gone?.querySelector('[data-testid="gardening-proposal-finding-not-live"]')).toBeTruthy();
  });

  it('renders a passed closure with its reason', async () => {
    const closure: ProposalClosureVm = {
      kind: 'passed',
      closedBy: 'u_1',
      closedAt: '2026-01-04T00:00:00Z',
      reason: 'not worth it yet',
    };
    const fixture = await mount({ vm: { ...BASE_VM, closure } });
    const el = fixture.nativeElement as HTMLElement;

    const closureEl = el.querySelector('[data-testid="gardening-proposal-closure-passed"]');
    expect(closureEl?.textContent).toContain('u_1');
    expect(closureEl?.textContent).toContain('not worth it yet');
    expect(el.querySelector('[data-testid="gardening-proposal-closure-accepted-minted"]')).toBeNull();
  });

  it('renders an accepted-and-minted closure with its work item link, repeated on each finding row', async () => {
    const closure: ProposalClosureVm = {
      kind: 'accepted',
      closedBy: 'u_1',
      closedAt: '2026-01-04T00:00:00Z',
      reason: null,
      workItem: { label: 'hub#42', webUrl: '/board/chunk/ch_1' },
    };
    const evidence: readonly ProposalEvidenceRowVm[] = EVIDENCE.map((row) => ({
      ...row,
      workItem: { label: 'hub#42', webUrl: '/board/chunk/ch_1' },
    }));
    const fixture = await mount({ vm: { ...BASE_VM, closure }, evidence });
    const el = fixture.nativeElement as HTMLElement;

    const closureEl = el.querySelector('[data-testid="gardening-proposal-closure-accepted-minted"]');
    const link = closureEl?.querySelector<HTMLAnchorElement>('[data-testid="gardening-proposal-work-item-link"]');
    expect(link?.textContent).toBe('hub#42');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
    expect(closureEl?.textContent).toContain("neither promotes this item nor changes any finding's state");

    const findingLink = el.querySelector('[data-testid="gardening-proposal-finding-work-item-link-fin_1"]');
    expect(findingLink?.textContent).toBe('hub#42');
  });

  it('degrades a null web_url to a label instead of a dead link', async () => {
    const closure: ProposalClosureVm = {
      kind: 'accepted',
      closedBy: 'u_1',
      closedAt: '2026-01-04T00:00:00Z',
      reason: null,
      workItem: { label: 'hub#42', webUrl: null },
    };
    const fixture = await mount({ vm: { ...BASE_VM, closure } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-work-item-link"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-work-item-label"]')?.textContent).toBe('hub#42');
  });

  it('renders an accepted-and-declined closure that says on the record it minted nothing', async () => {
    const closure: ProposalClosureVm = {
      kind: 'accepted',
      closedBy: 'u_1',
      closedAt: '2026-01-04T00:00:00Z',
      reason: 'already tracked elsewhere',
      workItem: null,
    };
    const fixture = await mount({ vm: { ...BASE_VM, closure } });
    const el = fixture.nativeElement as HTMLElement;

    const closureEl = el.querySelector('[data-testid="gardening-proposal-closure-accepted-declined"]');
    expect(closureEl?.querySelector('[data-testid="gardening-proposal-declined-mint"]')?.textContent).toContain(
      'no work item minted',
    );
    expect(closureEl?.textContent).toContain('already tracked elsewhere');
    expect(closureEl?.textContent).toContain("neither promotes an item nor changes any finding's state");
    expect(el.querySelector('[data-testid="gardening-proposal-work-item-link"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-work-item-label"]')).toBeNull();
  });

  it('offers Pass and Accept for a waiting proposal with chunk:control', async () => {
    const fixture = await mount({ canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-pass"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-accept"]')).toBeTruthy();
  });

  it('withholds Pass and Accept without chunk:control', async () => {
    const fixture = await mount({ canControl: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-actions"]')).toBeNull();
  });

  it('withholds Pass and Accept once a proposal is closed, even with chunk:control', async () => {
    const closure: ProposalClosureVm = {
      kind: 'passed',
      closedBy: 'u_1',
      closedAt: '2026-01-04T00:00:00Z',
      reason: 'not worth it yet',
    };
    const fixture = await mount({ vm: { ...BASE_VM, closure }, canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-actions"]')).toBeNull();
  });

  it('emits pass and accept', async () => {
    const fixture = await mount({ canControl: true });
    const el = fixture.nativeElement as HTMLElement;
    let passed = false;
    let accepted = false;
    fixture.componentInstance.pass.subscribe(() => (passed = true));
    fixture.componentInstance.accept.subscribe(() => (accepted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-pass"]')?.click();
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-accept"]')?.click();

    expect(passed).toBe(true);
    expect(accepted).toBe(true);
  });
});
