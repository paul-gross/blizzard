import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetFindingPanel, type FindingPanelTriageVerb, type FindingPanelVm } from './finding-panel';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const LIVE_VM: FindingPanelVm = {
  findingId: 'fin_01M1KANH0RZEABSD44RCEH6G9B',
  findingClass: 'stale-docstring',
  locus: 'src/a.py:1',
  state: 'live',
  observedCount: 3,
  introducedRev: '4ba7ef06d',
  introducedAt: null,
  firstObservedAt: '2026-01-01T00:00:00Z',
  lastSeenAt: '2026-01-05T00:00:00Z',
  summary: 'docstring narrates a removed parameter',
  note: null,
  workItem: null,
};

const RESOLVED_VM: FindingPanelVm = {
  ...LIVE_VM,
  findingId: 'fin_2',
  state: 'resolved',
  note: 'fixed in the same pass',
};

describe('FleetFindingPanel', () => {
  async function mount(inputs: {
    vm?: FindingPanelVm | null;
    state?: KitAsyncStateValue;
    canControl?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetFindingPanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetFindingPanel);
    fixture.componentRef.setInput('vm', inputs.vm ?? LIVE_VM);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('canControl', inputs.canControl ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders the select-a-finding rest state when nothing is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-finding-panel-empty"]')).toBeTruthy();
  });

  it('renders the class, locus, state, and observed count, with the full id in a title attribute', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-finding-panel"]')!;
    expect(panel.textContent).toContain('stale-docstring');
    expect(panel.textContent).toContain('src/a.py:1');
    expect(panel.querySelector('[data-testid="fp-state"]')?.textContent).toBe('live');
    expect(panel.querySelector('[data-testid="fp-observed"]')?.textContent).toContain('x3');
    expect(panel.querySelector('[title]')?.getAttribute('title')).toBe('fin_01M1KANH0RZEABSD44RCEH6G9B');
    expect(panel.textContent).toContain('F-6G9B');
  });

  it('renders the facts through the kit fact grid rather than a hand-rolled table', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const facts = el.querySelector('[data-testid="fp-facts"]')!;
    expect(facts.tagName.toLowerCase()).toBe('dl');
    expect(facts.closest('fleet-kit-fact-list')).toBeTruthy();
    expect(Array.from(facts.querySelectorAll('dt')).map((n) => n.textContent)).toEqual([
      'state',
      'observed',
      'introduced',
      'first observed',
      'last seen',
    ]);
    for (const testid of ['fp-state', 'fp-observed', 'fp-introduced', 'fp-first-observed', 'fp-last-seen']) {
      const dd = facts.querySelector(`[data-testid="${testid}"]`);
      expect(dd?.tagName.toLowerCase()).toBe('dd');
    }
    expect(el.querySelector('dl.fp-facts')).toBeNull();
  });

  it('leaves the panel styling its own markup inside the dd the kit emits', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    // Under emulated encapsulation an element is styleable only by the component
    // whose content attribute it carries — the whole reason these rows arrive as
    // templates rather than as projected content. The revision span must still carry
    // the panel's own attribute (so `.fp-rev` reaches it), while the `<dd>` around it
    // carries the kit's (so `.kv dd` owns the grid cell).
    const contentAttr = (node: Element) => node.getAttributeNames().filter((n) => n.startsWith('_ngcontent'));
    const locus = el.querySelector('.fp-locus')!;
    const rev = el.querySelector('[data-testid="fp-introduced"] .fp-rev')!;
    const dd = el.querySelector('[data-testid="fp-introduced"]')!;

    expect(contentAttr(locus).length).toBe(1);
    expect(contentAttr(dd).length).toBe(1);
    expect(contentAttr(rev)).toEqual(contentAttr(locus));
    expect(contentAttr(dd)).not.toEqual(contentAttr(locus));
  });

  it('renders introduced as a plain revision, never through fleet-when', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const introduced = el.querySelector('[data-testid="fp-introduced"]')!;
    expect(introduced.textContent).toContain('4ba7ef06d');
    expect(introduced.querySelector('fleet-when')).toBeNull();
  });

  it('renders last seen through fleet-when', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fp-last-seen"] fleet-when')).toBeTruthy();
  });

  it('renders introduced_at as unresolved rather than blank when the commit was never resolved', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const introduced = el.querySelector('[data-testid="fp-introduced"]')!;
    expect(introduced.textContent).toContain('4ba7ef06d');
    expect(introduced.querySelector('[data-testid="fp-introduced-at"]')).toBeNull();
    const unresolved = introduced.querySelector('[data-testid="fp-introduced-at-unresolved"]');
    expect(unresolved?.textContent).toContain('date unresolved');
    expect(unresolved?.getAttribute('title')).toContain('exactly one repository');
  });

  it('renders introduced_at through fleet-when once resolved', async () => {
    const fixture = await mount({
      vm: { ...LIVE_VM, introducedAt: '2026-01-02T00:00:00Z' },
    });
    const el = fixture.nativeElement as HTMLElement;

    const introduced = el.querySelector('[data-testid="fp-introduced"]')!;
    expect(introduced.querySelector('fleet-when[data-testid="fp-introduced-at"]')).toBeTruthy();
    expect(introduced.querySelector('[data-testid="fp-introduced-at-unresolved"]')).toBeNull();
  });

  it('renders first observed through fleet-when', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fp-first-observed"] fleet-when')).toBeTruthy();
  });

  it('renders first observed as a dash when absent', async () => {
    const fixture = await mount({ vm: { ...LIVE_VM, firstObservedAt: null } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fp-first-observed"]')?.textContent?.trim()).toBe('—');
  });

  it('renders the summary and any note as prose blocks', async () => {
    const fixture = await mount({ vm: RESOLVED_VM });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fp-summary"]')?.textContent).toContain(
      'docstring narrates a removed parameter',
    );
    expect(el.querySelector('[data-testid="fp-note"]')?.textContent).toContain('fixed in the same pass');
  });

  it('renders no note block when the finding carries none', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fp-note"]')).toBeNull();
  });

  it('renders a linked work item when present', async () => {
    const fixture = await mount({
      vm: { ...LIVE_VM, workItem: { label: 'hub#42', webUrl: '/board/chunk/ch_1' } },
    });
    const el = fixture.nativeElement as HTMLElement;

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="fp-work-item-link"]');
    expect(link?.textContent).toBe('hub#42');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
  });

  it('renders no CLI hint', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('hub finding show');
    expect(el.querySelector('.fp-cli')).toBeNull();
  });

  it('withholds every triage button without chunk:control', async () => {
    const fixture = await mount({ canControl: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-finding-panel-actions"]')).toBeNull();
  });

  it('offers the four exit verbs but not reopen for a still-open finding', async () => {
    const fixture = await mount({ canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-finding-panel-resolve"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-panel-confirm-gone"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-panel-wont-fix"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-panel-not-a-finding"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-panel-reopen"]')).toBeNull();
  });

  it('also offers reopen once the finding has exited', async () => {
    const fixture = await mount({ vm: RESOLVED_VM, canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-finding-panel-reopen"]')).toBeTruthy();
  });

  it('renders every triage verb as a call-to-action-sized button, matching the Proposals panel', async () => {
    const fixture = await mount({ vm: RESOLVED_VM, canControl: true });
    const el = fixture.nativeElement as HTMLElement;

    for (const testid of [
      'gardening-finding-panel-resolve',
      'gardening-finding-panel-confirm-gone',
      'gardening-finding-panel-wont-fix',
      'gardening-finding-panel-not-a-finding',
      'gardening-finding-panel-reopen',
    ]) {
      expect(el.querySelector(`[data-testid="${testid}"]`)?.classList.contains('cta')).toBe(true);
    }
  });

  it('emits triage with the clicked verb', async () => {
    const fixture = await mount({ canControl: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: FindingPanelTriageVerb | undefined;
    fixture.componentInstance.triage.subscribe((verb) => (emitted = verb));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-finding-panel-resolve"]')!.click();

    expect(emitted).toBe('resolve');
  });
});
