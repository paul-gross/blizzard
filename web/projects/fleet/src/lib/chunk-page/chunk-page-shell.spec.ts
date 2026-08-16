import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkPageShell } from './chunk-page-shell';

/**
 * A host projecting one marker element per slot, each with a distinct
 * `data-testid` so the rendered order is provable regardless of which
 * selector matched it — and, unlike `app-shell.spec.ts`'s own host (a review
 * finding against this refactor's sibling work: its markup already sat in
 * slot order, so the spec passed even against a component with no ordering
 * at all), this fixture's **markup order is the reverse of the slot order**:
 * body first, back last. A component that just rendered `<ng-content>` in
 * whatever order the caller wrote it would fail every assertion below.
 */
@Component({
  selector: 'fleet-chunk-page-shell-test-host',
  imports: [ChunkPageShell],
  template: `
    <fleet-chunk-page-shell>
      <div data-testid="slot-body">body</div>
      <div chunk-page-tabs data-testid="slot-tabs">tabs</div>
      <div chunk-page-header data-testid="slot-header">header</div>
      <div chunk-page-notice data-testid="slot-notice">notice</div>
      <div chunk-page-back data-testid="slot-back">back</div>
    </fleet-chunk-page-shell>
  `,
})
class ChunkPageShellTestHost {}

describe('ChunkPageShell', () => {
  it('orders the projected slots back, notice, header, tabs, body regardless of markup order', async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkPageShellTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkPageShellTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const cps = el.querySelector('.cps');
    expect(cps).not.toBeNull();

    const order = Array.from(cps!.querySelectorAll('[data-testid]')).map((node) => node.getAttribute('data-testid'));
    expect(order).toEqual(['slot-back', 'slot-notice', 'slot-header', 'slot-tabs', 'slot-body']);
  });

  it('places the back node before every other slot in document order (compareDocumentPosition)', async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkPageShellTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkPageShellTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const back = el.querySelector('[data-testid="slot-back"]')!;
    const notice = el.querySelector('[data-testid="slot-notice"]')!;
    const header = el.querySelector('[data-testid="slot-header"]')!;
    const tabs = el.querySelector('[data-testid="slot-tabs"]')!;
    const body = el.querySelector('[data-testid="slot-body"]')!;

    const precedes = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(precedes(back, notice)).toBe(true);
    expect(precedes(notice, header)).toBe(true);
    expect(precedes(header, tabs)).toBe(true);
    expect(precedes(tabs, body)).toBe(true);
  });

  it('renders only the slots given content — an absent slot leaves no empty marker behind', async () => {
    @Component({
      selector: 'fleet-chunk-page-shell-partial-host',
      imports: [ChunkPageShell],
      template: `
        <fleet-chunk-page-shell>
          <div chunk-page-back data-testid="slot-back">back</div>
          <div data-testid="slot-body">body</div>
        </fleet-chunk-page-shell>
      `,
    })
    class PartialHost {}

    await TestBed.configureTestingModule({
      imports: [PartialHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(PartialHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="slot-back"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="slot-body"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="slot-notice"]')).toBeNull();
    expect(el.querySelector('[data-testid="slot-header"]')).toBeNull();
    expect(el.querySelector('[data-testid="slot-tabs"]')).toBeNull();
  });

  /**
   * The shared spacing contract itself (issue: the runner's own page used to
   * carry a `padding: 8px; gap: 8px` its `.page` class layered on top of this
   * chrome, reading as a border of dead space the hub never had). Pinned here,
   * on the shell's own container, rather than per-page: since a page no longer
   * gets a wrapper div of its own to put a padding/gap declaration on (its
   * back bar, header, tabs, and body are all projected straight into `.cps`),
   * the *only* place that regression can recur is the shell itself — so this
   * is where re-introducing it would be caught.
   */
  it('owns the flush, gap-free outer chrome — no padding or gap on the outer column', async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkPageShellTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkPageShellTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const cps = el.querySelector('.cps') as HTMLElement;
    const style = getComputedStyle(cps);
    expect(style.display).toBe('flex');
    expect(style.flexDirection).toBe('column');
    expect(style.overflow).toBe('hidden');
    // jsdom's `getComputedStyle` only reports a value for a property the
    // component's own stylesheet actually declares — an empty string here
    // means "not set", which is exactly the no-padding/no-gap contract this
    // pins: a future edit that adds `padding`/`gap` to `.cps` turns these
    // into real, non-empty values and fails the assertion.
    expect(style.padding).toBe('');
    expect(style.gap).toBe('');
  });

  /** The active-tab body's own chrome — flex:1/min-height:0 so it absorbs the
   * column's remaining space, and `position: relative` so a projected
   * `KitAsyncState`'s absolutely-centered status line has a box to center in
   * (the runner page's own `.body` used to own this; now the shell does, for
   * both pages alike). */
  it('gives the body slot a positioned, flexible box for a projected async-state to center in', async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkPageShellTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkPageShellTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const body = el.querySelector('.cps-body') as HTMLElement;
    expect(body).not.toBeNull();
    const style = getComputedStyle(body);
    expect(style.position).toBe('relative');
    expect(style.flexGrow).toBe('1');
    expect(style.minHeight).toBe('0px');
  });
});
