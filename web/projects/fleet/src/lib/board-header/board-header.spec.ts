import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { hiddenAtContainerWidth } from 'fleet/testing';

import type { ChunkStatus, ChunkSummary } from '../api/hub';
import { LANES, STATUS_LANE } from '../chunk-lanes';
import { BoardHeader } from './board-header';

const chunk = (id: string, status: ChunkSummary['status']): ChunkSummary => ({
  chunk_id: id,
  graph_id: 'gr_1',
  status,
  current_node_id: 'nd_build',
  work_refs: [],
});

describe('BoardHeader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardHeader],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  const render = async (chunks: ChunkSummary[], connection = 'ok') => {
    const fixture = TestBed.createComponent(BoardHeader);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('connection', connection);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  };

  it('reflects the connection input', async () => {
    const el = await render([], 'reconnecting…');
    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('reconnecting…');
  });

  it('counts the fleet into its lanes, folding the transient and terminal states in', async () => {
    // `delivering` is a running chunk mid-hand-off and `stopped` a terminal one — the
    // board shows each under RUNNING and DONE, and the header must agree with the board
    // rather than invent two more lanes for states an operator does not act on.
    const el = await render([
      chunk('ch_1', 'ready'),
      chunk('ch_2', 'running'),
      chunk('ch_3', 'delivering'),
      chunk('ch_4', 'waiting_on_human'),
      chunk('ch_5', 'needs_human'),
      chunk('ch_6', 'done'),
      chunk('ch_7', 'stopped'),
      chunk('ch_8', 'not_ready'),
    ]);

    const stat = (key: string) => el.querySelector(`[data-testid="stat-${key}"]`)?.textContent?.trim();
    expect(stat('total')).toBe('8');
    expect(stat('ready')).toBe('1');
    expect(stat('notready')).toBe('1');
    expect(stat('running')).toBe('2');
    expect(stat('waiting')).toBe('1');
    expect(stat('needs')).toBe('1');
    expect(stat('done')).toBe('2');
  });

  it('shows one cell per board lane, in the board\'s order — it cannot list a lane the board lacks', async () => {
    // The header is grouped through the same LANES the board columns are built from,
    // so the two cannot drift apart; a lane added there appears in both or neither.
    const el = await render([]);
    const cells = [...el.querySelectorAll('[data-stat]')].map((c) => c.getAttribute('data-stat'));
    expect(cells).toEqual(['total', 'ready', ...LANES.map((l) => l.key)]);
  });

  it('counts every wire status into some cell, so none can go silently missing', async () => {
    // The exhaustive Record in chunk-lanes makes a new status a compile error there;
    // this asserts the consequence — the lanes plus the ready rail account for the
    // whole fleet, so total always equals the sum of the cells beside it.
    const everyStatus = Object.keys(STATUS_LANE) as ChunkStatus[];
    const el = await render(everyStatus.map((s, i) => chunk(`ch_${i}`, s)));

    const value = (key: string) =>
      Number(el.querySelector(`[data-testid="stat-${key}"]`)?.textContent?.trim() ?? '0');
    const lanesAndReady = value('ready') + LANES.reduce((sum, lane) => sum + value(lane.key), 0);
    expect(lanesAndReady).toBe(everyStatus.length);
    expect(value('total')).toBe(everyStatus.length);
  });

  it('rests at zero for an idle fleet', async () => {
    const el = await render([]);
    expect(el.querySelector('[data-testid="stat-total"]')?.textContent?.trim()).toBe('0');
    expect(el.querySelector('[data-testid="stat-needs"]')?.textContent?.trim()).toBe('0');
  });

  it('shows no spend-today cell before the fleet-spend read resolves (issue #60)', async () => {
    const el = await render([]);
    expect(el.querySelector('[data-testid="spend-today"]')).toBeNull();
  });

  it("renders the fleet's spend-today figure once the read resolves (issue #60)", async () => {
    const fixture = TestBed.createComponent(BoardHeader);
    fixture.componentRef.setInput('chunks', []);
    fixture.componentRef.setInput('spendToday', {
      since: '2026-07-17T00:00:00Z',
      input_tokens: 100,
      output_tokens: 50,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: 3.5,
      cost_partial: false,
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="spend-today-value"]')?.textContent).toContain('$3.50');
  });

  it('renders explicit stat cells in place of the chunk-derived ones, as a capacity fraction (issue #131)', async () => {
    const fixture = TestBed.createComponent(BoardHeader);
    fixture.componentRef.setInput('chunks', [chunk('ch_1', 'ready')]);
    fixture.componentRef.setInput('stats', [
      { key: 'envs', label: 'Envs', value: 2, capacity: 4 },
      { key: 'agents', label: 'Agents', value: 1, capacity: 2 },
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="stat-envs"]')?.textContent?.trim()).toBe('2/4');
    expect(el.querySelector('[data-testid="stat-agents"]')?.textContent?.trim()).toBe('1/2');
    // The chunk-derived lane cells (e.g. `total`) do not also render — `stats`
    // replaces them entirely rather than appending to them.
    expect(el.querySelector('[data-testid="stat-total"]')).toBeNull();
  });

  it('reflects connectionLabel and tagline overrides, defaulting to the hub\'s own text', async () => {
    const defaultEl = await render([]);
    expect(defaultEl.querySelector('[data-testid="conn"]')?.textContent).toContain('Hub');
    expect(defaultEl.querySelector('.brand-text')?.textContent).toContain('fleet hub · mission control');

    const fixture = TestBed.createComponent(BoardHeader);
    fixture.componentRef.setInput('connectionLabel', 'Runner');
    fixture.componentRef.setInput('tagline', 'runner · machine panel');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('Runner');
    expect(el.querySelector('.brand-text')?.textContent).toContain('runner · machine panel');
  });

  it('marks the spend-today figure with the lower-bound prefix when PARTIAL (issue #60)', async () => {
    const fixture = TestBed.createComponent(BoardHeader);
    fixture.componentRef.setInput('chunks', []);
    fixture.componentRef.setInput('spendToday', {
      since: '2026-07-17T00:00:00Z',
      input_tokens: 100,
      output_tokens: 50,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: 0.1,
      cost_partial: true,
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="spend-today-value"]')?.textContent).toContain('~$0.10');
  });

  /*
   * The tiered collapse (issue #163). jsdom parses `@container` rules but never
   * evaluates them, so these resolve the component's own shipped rules at a
   * given container width through `resolveContainerStyle` rather than trusting
   * `getComputedStyle`, which would report the wide-tier value at every width.
   */
  describe('responsive collapse (issue #163)', () => {
    const at = (el: HTMLElement, selector: string, width: number) =>
      hiddenAtContainerWidth(el.querySelector(selector)!, { containerName: 'board-header', width });

    const spendRender = async () => {
      const fixture = TestBed.createComponent(BoardHeader);
      fixture.componentRef.setInput('chunks', [chunk('ch_1', 'ready')]);
      fixture.componentRef.setInput('spendToday', {
        since: '2026-07-17T00:00:00Z',
        input_tokens: 100,
        output_tokens: 50,
        cache_read_tokens: 0,
        cache_create_tokens: 0,
        cost_usd: 3.5,
        cost_partial: false,
      });
      await fixture.whenStable();
      return fixture.nativeElement as HTMLElement;
    };

    it('queries its own header width, not the viewport — so the two shells collapse independently', async () => {
      const el = await render([]);
      const header = el.querySelector<HTMLElement>('.mc-header')!;
      expect(getComputedStyle(header).containerName).toBe('board-header');
      expect(getComputedStyle(header).containerType).toBe('inline-size');
    });

    it('shows every region at the full tier', async () => {
      const el = await spendRender();
      expect(at(el, '[data-testid="board-header-stats"]', 1200)).toBe(false);
      expect(at(el, '[data-testid="spend-today"]', 1200)).toBe(false);
      expect(at(el, '.brand-text', 1200)).toBe(false);
      expect(at(el, '[data-testid="conn"]', 1200)).toBe(false);
      expect(at(el, '.trailing', 1200)).toBe(false);
    });

    it('drops the stat strip at the mid tier, keeping spend, brand, connection, and the menu slot', async () => {
      const el = await spendRender();
      expect(at(el, '[data-testid="board-header-stats"]', 1000)).toBe(true);
      expect(at(el, '[data-testid="spend-today"]', 1000)).toBe(false);
      expect(at(el, '.brand-text', 1000)).toBe(false);
      expect(at(el, '[data-testid="conn"]', 1000)).toBe(false);
      expect(at(el, '.trailing', 1000)).toBe(false);
    });

    it('drops spend and the whole brand text at the narrow tier — never the mark, connection cell, or menu slot', async () => {
      const el = await spendRender();
      expect(at(el, '[data-testid="board-header-stats"]', 420)).toBe(true);
      expect(at(el, '[data-testid="spend-today"]', 420)).toBe(true);
      // The whole text block, tagline included — the tagline now goes with its
      // parent rather than by a rule of its own, which is why this asserts the
      // block: `hiddenAtContainerWidth` answers for one element's own resolved
      // display, not for ancestors collapsing above it.
      expect(at(el, '.brand-text', 420)).toBe(true);
      // The one guarantee the whole tiering exists for: on a phone forced into
      // desktop mode, the profile menu behind this slot is the only way back.
      expect(at(el, '[data-testid="conn"]', 420)).toBe(false);
      expect(at(el, '.trailing', 420)).toBe(false);
      expect(at(el, 'fleet-brand-mark', 420)).toBe(false);
    });

    it('lets the stat strip clip rather than force the row wider than its shell', async () => {
      const el = await render([]);
      const strip = el.querySelector<HTMLElement>('[data-testid="board-header-stats"]')!;
      // `min-width: 0` + `overflow: hidden` is what lets the strip clip instead
      // of forcing the row wider than the viewport-locked shell.
      expect(getComputedStyle(strip).minWidth).toBe('0px');
      expect(getComputedStyle(strip).overflow).toBe('hidden');
      expect(getComputedStyle(strip).flexShrink).toBe('1');

      // The brand and the connection cell are fixed-width chrome and never give.
      for (const selector of ['.brand', '[data-testid="conn"]']) {
        expect(getComputedStyle(el.querySelector<HTMLElement>(selector)!).flexShrink).toBe('0');
      }
    });

    it('lets the trailing cluster shrink, so a consumer can truncate content-sized controls', async () => {
      const el = await render([]);
      const trailing = el.querySelector<HTMLElement>('.trailing')!;

      // Pinning this `flex: none` would size it to max-content, and a consumer
      // projecting something content-sized (the runner's signed-in username)
      // would then push its own menu off the clipped right edge no matter how
      // narrow the header got — a hard breakpoint cannot cover a width that
      // depends on the data. Shrinkable here, with each projected control
      // deciding for itself whether it gives way (`local-panel-layout.ts`).
      expect(getComputedStyle(trailing).minWidth).toBe('0px');
      expect(getComputedStyle(trailing).flexShrink).toBe('1');
    });
  });
});
