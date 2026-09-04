import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';

import {
  FleetProposalPanel,
  type ProposalClosureVm,
  type ProposalEvidenceRowVm,
  type ProposalPanelVm,
} from './proposal-panel';

const BASE_VM: ProposalPanelVm = {
  proposalId: 'gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2',
  routineName: 'comments',
  proposalClass: 'fix-the-source',
  title: 'Author a docstring standard',
  body: 'Seventeen modules narrate their own change history.',
  closure: null,
  createdAt: new Date(2026, 6, 18, 9, 5, 3).toISOString(),
};

const EVIDENCE: readonly ProposalEvidenceRowVm[] = [
  { findingId: 'fin_1', locus: 'src/a.py:1', summary: 'stale docstring', state: 'live', workItem: null },
  { findingId: 'fin_2', locus: 'src/b.py:9', summary: 'another one', state: 'resolved', workItem: null },
  // `gone` is the state that separates "has exited" from the wire's `live` boolean:
  // still open, still triageable, and the only row Confirm gone is actually for.
  { findingId: 'fin_3', locus: 'src/c.py:4', summary: 'ground moved', state: 'gone', workItem: null },
];

describe('FleetProposalPanel', () => {
  beforeEach(() => {
    vi.setSystemTime(new Date(2026, 6, 18, 15, 30));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function mount(inputs: {
    vm?: ProposalPanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    evidence?: readonly ProposalEvidenceRowVm[];
    evidenceState?: 'loading' | 'error' | 'empty' | 'ready';
    canControl?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetProposalPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
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

  it('sits inside the kit panel shell, labelled Proposal', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-proposal-detail-panel"]');
    expect(panel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(panel?.textContent).toContain('Proposal');
  });

  it('renders the header title and meta line, with the full id in a title attribute', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const caseEl = el.querySelector('[data-testid="gardening-proposal-case"]');
    expect(caseEl?.textContent).toContain('Author a docstring standard');
    expect(caseEl?.textContent).toContain('fix-the-source');
    expect(caseEl?.textContent).toContain('GP-X1Y2');

    const meta = caseEl?.querySelector('.pp-meta');
    const refSpan = meta?.firstElementChild;
    expect(refSpan?.textContent?.trim()).toBe('GP-X1Y2');
    expect(refSpan?.getAttribute('title')).toBe('gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2');
  });

  it('renders the panel meta line as one uniform id-first sequence: ref, class, routine, created', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const meta = el.querySelector('[data-testid="gardening-proposal-case"] .pp-meta');
    const spans = Array.from(meta?.querySelectorAll('span') ?? []).map((s) => s.textContent?.trim());
    expect(spans).toEqual(['GP-X1Y2', 'fix-the-source', 'comments', '09:05']);
    expect(el.querySelector('.pp-ref')).toBeNull();
  });

  it('renders the body through the prose block', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('Seventeen modules narrate their own change history.');
  });

  it('renders findings from the live evidence read, not from any proposal-carried text, each linked by compact ref', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const live = el.querySelector('[data-testid="gardening-proposal-finding-fin_1"]');
    expect(live?.textContent).toContain('src/a.py:1');
    expect(live?.textContent).toContain('stale docstring');

    const link = live?.querySelector<HTMLAnchorElement>('[data-testid="gardening-proposal-finding-link-fin_1"]');
    expect(link?.textContent).toBe('F-1');
    expect(link?.getAttribute('href')).toBe('/gardening/findings/fin_1');
  });

  it("names each evidence row's own state inline, off the shared mapping", async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(
      el.querySelector('[data-testid="gardening-proposal-finding-state-fin_1"]')?.textContent?.trim(),
    ).toBe('live');
    expect(
      el.querySelector('[data-testid="gardening-proposal-finding-state-fin_2"]')?.textContent?.trim(),
    ).toBe('resolved');

    // Distinct tones, so a live row and an exited one never read alike.
    const toneOf = (id: string) =>
      el.querySelector(`[data-testid="gardening-proposal-finding-state-${id}"] .badge`)?.getAttribute('style');
    expect(toneOf('fin_1')).toBeTruthy();
    expect(toneOf('fin_1')).not.toBe(toneOf('fin_2'));
  });

  describe('inline evidence triage', () => {
    it('offers every exit verb on a live row', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;

      for (const verb of ['resolve', 'confirm-gone', 'wont-fix', 'not-a-finding']) {
        expect(
          el.querySelector(`[data-testid="gardening-proposal-finding-${verb}-fin_1"]`),
          verb,
        ).toBeTruthy();
      }
    });

    it('withholds them entirely without chunk:control, rather than offering a button that 403s', async () => {
      const fixture = await mount({ canControl: false });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-proposal-finding-resolve-fin_1"]')).toBeNull();
    });

    it('withholds the actions on a row that already left the bucket', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;

      // `fin_2` is resolved — an exit verb has nothing left to do to it.
      expect(el.querySelector('[data-testid="gardening-proposal-finding-resolve-fin_2"]')).toBeNull();
      expect(el.querySelector('[data-testid="gardening-proposal-finding-state-fin_2"]')).toBeTruthy();
    });

    it('still offers every verb on a gone-flagged row, which has not exited', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;

      // `fin_3` is `gone`: `FindingView.live` would read `false` here, but the row is
      // still open (D8) and Confirm gone is the verb it exists to receive. Gating off
      // `live` rather than `state` would withhold exactly this row's own verb.
      for (const verb of ['resolve', 'confirm-gone', 'wont-fix', 'not-a-finding']) {
        expect(
          el.querySelector(`[data-testid="gardening-proposal-finding-${verb}-fin_3"]`),
          verb,
        ).toBeTruthy();
      }
    });

    it('emits the finding and the verb, dispatching nothing itself', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;
      const emitted: { findingId: string; verb: string }[] = [];
      fixture.componentInstance.evidenceTriage.subscribe((e) => emitted.push({ ...e }));

      el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-finding-wont-fix-fin_1"]')!.click();

      expect(emitted).toEqual([{ findingId: 'fin_1', verb: 'wont-fix' }]);
    });
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

  it('offers Pass and Accept for a waiting proposal with chunk:control, both as cta-sized buttons', async () => {
    const fixture = await mount({ canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    const pass = el.querySelector('[data-testid="gardening-proposal-pass"]');
    const accept = el.querySelector('[data-testid="gardening-proposal-accept"]');
    expect(pass).toBeTruthy();
    expect(accept).toBeTruthy();
    expect(pass?.classList.contains('cta')).toBe(true);
    expect(accept?.classList.contains('cta')).toBe(true);
    expect(accept?.classList.contains('primary')).toBe(true);
  });

  it('renders the pass/accept CLI hints as an aligned fact grid above the action bar', async () => {
    const fixture = await mount({ canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    const cli = el.querySelector('[data-testid="gardening-proposal-cli"]');
    expect(cli?.tagName.toLowerCase()).toBe('dl');
    expect(cli?.textContent).toContain('hub garden-proposal pass gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2');
    expect(cli?.textContent).toContain('hub garden-proposal accept gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2');

    const actions = el.querySelector('[data-testid="gardening-proposal-actions"]');
    const cliIndex = Array.from(actions?.children ?? []).findIndex((c) =>
      c.querySelector('[data-testid="gardening-proposal-cli"]'),
    );
    const barIndex = Array.from(actions?.children ?? []).findIndex((c) =>
      c.querySelector('[data-testid="gardening-proposal-pass"]'),
    );
    expect(cliIndex).toBeGreaterThanOrEqual(0);
    expect(barIndex).toBeGreaterThan(cliIndex);
  });

  it('no longer offers a read-with CLI hint under Evidence', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const evidence = el.querySelector('[data-testid="gardening-proposal-evidence"]');
    expect(evidence?.textContent).not.toContain('hub finding show');
  });

  it("renders each evidence row's summary through a prose block, formatting preserved", async () => {
    const evidence: readonly ProposalEvidenceRowVm[] = [
      { findingId: 'fin_1', locus: 'src/a.py:1', summary: 'line one\nline two', state: 'live', workItem: null },
    ];
    const fixture = await mount({ evidence });
    const el = fixture.nativeElement as HTMLElement;

    const summary = el.querySelector('[data-testid="gardening-proposal-finding-summary-fin_1"] .tx');
    expect(summary?.textContent).toBe('line one\nline two');
  });

  it("renders a closure's reason through a prose block", async () => {
    const closure: ProposalClosureVm = {
      kind: 'passed',
      closedBy: 'u_1',
      closedAt: '2026-01-04T00:00:00Z',
      reason: 'not worth it yet',
    };
    const fixture = await mount({ vm: { ...BASE_VM, closure } });
    const el = fixture.nativeElement as HTMLElement;

    const reason = el.querySelector('[data-testid="gardening-proposal-closure-reason"] .tx');
    expect(reason?.textContent).toBe('not worth it yet');
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
