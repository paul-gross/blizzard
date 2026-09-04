import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GraphExplorerList } from './graph-explorer-list';

const GRAPHS = [
  { graph_id: 'gr_build_v2', name: 'build', created_at: '2026-07-18T00:00:00Z', effective: true, entry_node_id: 'n1' },
  { graph_id: 'gr_build_v1', name: 'build', created_at: '2026-07-01T00:00:00Z', effective: false, entry_node_id: 'n1' },
  { graph_id: 'gr_review_v1', name: 'review', created_at: '2026-07-10T00:00:00Z', effective: true, entry_node_id: 'n2' },
];

describe('GraphExplorerList', () => {
  async function mount(graphs: unknown[] = GRAPHS, selectedGraphId: string | null = null) {
    await TestBed.configureTestingModule({
      imports: [GraphExplorerList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphExplorerList);
    fixture.componentRef.setInput('graphs', graphs);
    fixture.componentRef.setInput('selectedGraphId', selectedGraphId);
    await fixture.whenStable();
    return fixture;
  }

  it('groups graphs by name and shows the version count + effective summary as compact refs', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    const groups = el.querySelectorAll('[data-testid="graph-explorer-group"]');
    expect(groups).toHaveLength(2);

    const buildGroup = el.querySelector('[data-name="build"]');
    expect(buildGroup?.querySelector('[data-testid="graph-explorer-group-count"]')?.textContent?.trim()).toBe('(2)');
    expect(
      buildGroup?.querySelector('[data-testid="graph-explorer-group-effective"]')?.textContent?.trim(),
    ).toBe('G-d_v2');
    // No long id leaks into the group row.
    expect(buildGroup?.textContent).not.toContain('gr_build_v2');
  });

  it('reveals the lineage newest-first with effective/superseded badges on expand', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-lineage"]')).toBeNull();

    const buildToggle = el.querySelector<HTMLButtonElement>(
      '[data-name="build"] [data-testid="graph-explorer-group-toggle"]',
    );
    buildToggle?.click();
    await fixture.whenStable();

    const rows = el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]');
    expect(rows).toHaveLength(2);
    // The row's own testid lands on the selectable `fleet-kit-select-row` button
    // (`kit-select-row.ts`'s own contract); `data-graph-id` rides its wrapping
    // `<li>` one level up, `run-list.ts`'s `data-name` shape on the group `<li>`.
    expect(rows[0].closest('[data-graph-id]')?.getAttribute('data-graph-id')).toBe('gr_build_v2');
    expect(rows[1].closest('[data-graph-id]')?.getAttribute('data-graph-id')).toBe('gr_build_v1');

    // Compact ref, not the long id (issue #206).
    expect(rows[0].querySelector('[data-testid="graph-explorer-graph-id"]')?.textContent?.trim()).toBe('G-d_v2');
    expect(rows[1].querySelector('[data-testid="graph-explorer-graph-id"]')?.textContent?.trim()).toBe('G-d_v1');
    // The shared relative-date component, not a raw ISO string.
    expect(rows[0].querySelector('[data-testid="graph-explorer-created-at"]')?.textContent?.trim()).not.toBe(
      GRAPHS[0].created_at,
    );
    expect(rows[0].querySelector('fleet-when[data-testid="graph-explorer-created-at"]')).toBeTruthy();

    expect(rows[0].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('effective');
    expect(rows[1].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('superseded');
    // Exact vocabulary — never "active"/"disabled".
    expect(el.textContent).not.toContain('active');
    expect(el.textContent).not.toContain('disabled');
  });

  it('emits selectGraph for either an effective or a superseded row', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    const emitted: string[] = [];
    fixture.componentInstance.selectGraph.subscribe((id: string) => emitted.push(id));

    // The clickable element is the row's `fleet-kit-select-row` button, nested
    // inside the `<li data-graph-id>` wrapper — clicking the wrapper itself would
    // not reach it (it is a descendant, not the wrapper's own listener target).
    el.querySelector<HTMLButtonElement>('[data-graph-id="gr_build_v1"] [data-testid="graph-explorer-row"]')?.click();
    await fixture.whenStable();

    expect(emitted).toEqual(['gr_build_v1']);
  });

  it('selects the group\'s effective version when its header expands the group (issue #152)', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    const emitted: string[] = [];
    fixture.componentInstance.selectGraph.subscribe((id: string) => emitted.push(id));

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    // The effective version — the id the header already displayed — not the newest row.
    expect(emitted).toEqual(['gr_build_v2']);
    expect(el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]')).toHaveLength(2);
  });

  it('falls back to the newest version when no version of a group is marked effective', async () => {
    const fixture = await mount([
      { ...GRAPHS[0], effective: false },
      { ...GRAPHS[1], effective: false },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    const emitted: string[] = [];
    fixture.componentInstance.selectGraph.subscribe((id: string) => emitted.push(id));

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    expect(emitted).toEqual(['gr_build_v2']);
  });

  it('collapses on a second header click and leaves the selection untouched', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    const emitted: string[] = [];
    fixture.componentInstance.selectGraph.subscribe((id: string) => emitted.push(id));

    const toggle = el.querySelector<HTMLButtonElement>(
      '[data-name="build"] [data-testid="graph-explorer-group-toggle"]',
    );
    toggle?.click();
    await fixture.whenStable();
    // The parent routes on the emission and feeds the selection back in — the state the
    // collapsing click actually runs against.
    fixture.componentRef.setInput('selectedGraphId', 'gr_build_v2');
    await fixture.whenStable();

    toggle?.click();
    await fixture.whenStable();

    // Collapsed even though the selection still lives in this group, and no second
    // emission: a header click only ever adds a selection, never clears or changes one.
    expect(el.querySelector('[data-name="build"] [data-testid="graph-explorer-lineage"]')).toBeNull();
    expect(emitted).toEqual(['gr_build_v2']);
  });

  it('reveals a deep-linked version even in a group the operator had collapsed', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;
    const toggle = el.querySelector<HTMLButtonElement>(
      '[data-name="build"] [data-testid="graph-explorer-group-toggle"]',
    );

    // Expand (selects gr_build_v2), then collapse — the override the collapse records
    // must not outlive the navigation that follows it.
    toggle?.click();
    await fixture.whenStable();
    fixture.componentRef.setInput('selectedGraphId', 'gr_build_v2');
    await fixture.whenStable();
    toggle?.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-name="build"] [data-testid="graph-explorer-lineage"]')).toBeNull();

    // Deep-link to the *superseded* version inside that same collapsed group.
    fixture.componentRef.setInput('selectedGraphId', 'gr_build_v1');
    await fixture.whenStable();

    expect(el.querySelector('[data-name="build"] [data-testid="graph-explorer-lineage"]')).toBeTruthy();
    expect(
      el.querySelector('[data-graph-id="gr_build_v1"] [data-testid="graph-explorer-row"]')?.classList,
    ).toContain('selected');
  });

  it('keeps a collapsed group shut when the selection moves to a different group', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();
    fixture.componentRef.setInput('selectedGraphId', 'gr_build_v2');
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    // A selection landing elsewhere is no statement about this group — it stays shut.
    fixture.componentRef.setInput('selectedGraphId', 'gr_review_v1');
    await fixture.whenStable();

    expect(el.querySelector('[data-name="build"] [data-testid="graph-explorer-lineage"]')).toBeNull();
  });

  it('still reveals the lineage of a deep-linked version the operator never toggled', async () => {
    const fixture = await mount(GRAPHS, 'gr_build_v1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-name="build"] [data-testid="graph-explorer-lineage"]')).toBeTruthy();
    expect(el.querySelector('[data-name="review"] [data-testid="graph-explorer-lineage"]')).toBeNull();
  });

  it('shows a retired badge for a retired, non-effective version (issue #101)', async () => {
    const graphs = [
      { ...GRAPHS[0], effective: false },
      { ...GRAPHS[1], effective: true, retired: false },
      GRAPHS[2],
    ];
    // gr_build_v2 (newest) is retired; gr_build_v1 (older) is now effective.
    const fixture = await mount([{ ...graphs[0], retired: true }, graphs[1], graphs[2]]);
    const el = fixture.nativeElement as HTMLElement;

    // Retired versions are filtered out by default, so the badge is only reachable
    // with the filter on.
    el.querySelector<HTMLButtonElement>('[data-testid="graph-explorer-show-retired"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    const rows = el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]');
    expect(rows[0].closest('[data-graph-id]')?.getAttribute('data-graph-id')).toBe('gr_build_v2');
    expect(rows[0].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('retired');
    expect(rows[1].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('effective');
  });

  it('filters retired versions out of a lineage by default, and brings them back with the filter', async () => {
    const graphs = [
      { ...GRAPHS[0], effective: false, retired: true },
      { ...GRAPHS[1], effective: true },
      GRAPHS[2],
    ];
    const fixture = await mount(graphs);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    expect(el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-name="build"] [data-testid="graph-explorer-group-count"]')?.textContent?.trim()).toBe(
      '(1)',
    );

    el.querySelector<HTMLButtonElement>('[data-testid="graph-explorer-show-retired"]')?.click();
    await fixture.whenStable();

    expect(el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]')).toHaveLength(2);
  });

  it('names how many versions the filter is holding back, and stops saying so once they are shown', async () => {
    const fixture = await mount([{ ...GRAPHS[0], effective: false, retired: true }, GRAPHS[1], GRAPHS[2]]);
    const el = fixture.nativeElement as HTMLElement;

    const chip = el.querySelector('[data-testid="graph-explorer-show-retired"]');
    expect(chip?.textContent).toContain('1 hidden');

    chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="graph-explorer-show-retired"]')?.textContent).not.toContain('hidden');
  });

  it('drops a group entirely when every one of its versions is retired', async () => {
    const fixture = await mount([
      { ...GRAPHS[0], effective: false, retired: true },
      { ...GRAPHS[1], effective: false, retired: true },
      GRAPHS[2],
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-name="build"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="graph-explorer-group"]')).toHaveLength(1);
  });

  it('keeps a retired version visible while it is the open selection, so a deep link still finds its row', async () => {
    const fixture = await mount(
      [{ ...GRAPHS[0], effective: false, retired: true }, GRAPHS[1], GRAPHS[2]],
      'gr_build_v2',
    );
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].closest('[data-graph-id]')?.getAttribute('data-graph-id')).toBe('gr_build_v2');
    // Kept for the selection, so it is not also counted as hidden.
    expect(el.querySelector('[data-testid="graph-explorer-show-retired"]')?.textContent).not.toContain('hidden');
  });

  it('renders no groups for an empty graph list', async () => {
    const fixture = await mount([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="graph-explorer-group"]')).toHaveLength(0);
  });

  it('builds both the group header and every lineage row on the shared fleet-kit-select-row', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    // One selectable row per group header, before any expansion.
    expect(el.querySelectorAll('fleet-kit-select-row')).toHaveLength(2);

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    // Expanding a group adds one `fleet-kit-select-row` per lineage row underneath it.
    expect(el.querySelectorAll('fleet-kit-select-row')).toHaveLength(4);
  });

  it("selects the group header's own row when its effective version is the one open", async () => {
    const fixture = await mount(GRAPHS, 'gr_build_v2');
    const el = fixture.nativeElement as HTMLElement;

    const header = el.querySelector('[data-name="build"] [data-testid="graph-explorer-group-toggle"]');
    expect(header?.classList).toContain('selected');

    // A group whose effective version is *not* the open one shows no selected chrome.
    const otherHeader = el.querySelector('[data-name="review"] [data-testid="graph-explorer-group-toggle"]');
    expect(otherHeader?.classList).not.toContain('selected');
  });

  it("renders a lineage row's badge on fleet-kit-badge, colored by lifecycle tone", async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await fixture.whenStable();

    const badge = el.querySelector('[data-testid="graph-explorer-badge"]');
    expect(badge?.tagName.toLowerCase()).toBe('fleet-kit-badge');
    expect(badge?.textContent?.trim()).toBe('effective');
  });
});
