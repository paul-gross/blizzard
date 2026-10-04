import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { AppShell } from './app-shell';

/** A host projecting one marker element per slot, each with a distinct
 * `data-testid` — and, critically, authored in an order *different* from
 * the slot order AppShell enforces (tab-bar, content, nav, header). Markup
 * order matching slot order can't tell AppShell's selected `ng-content`
 * slots apart from a single default one that just projects in document
 * order, so every assertion in this spec renders through this fixture. */
@Component({
  selector: 'fleet-app-shell-scrambled-host',
  imports: [AppShell],
  template: `
    <fleet-app-shell>
      <div shell-tab-bar data-testid="slot-tab-bar">tab-bar</div>
      <div data-testid="slot-content">content</div>
      <div shell-nav data-testid="slot-nav">nav</div>
      <div shell-header data-testid="slot-header">header</div>
    </fleet-app-shell>
  `,
})
class ScrambledHost {}

describe('AppShell', () => {
  it('orders the projected slots header, nav, content, tab-bar even when markup order differs', async () => {
    await TestBed.configureTestingModule({
      imports: [ScrambledHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ScrambledHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const shell = el.querySelector('.shell');
    expect(shell).not.toBeNull();

    const order = Array.from(shell!.querySelectorAll('[data-testid]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(order).toEqual(['slot-header', 'slot-nav', 'slot-content', 'slot-tab-bar']);
  });

  it('places every slot before the next in document order (compareDocumentPosition), despite markup order', async () => {
    await TestBed.configureTestingModule({
      imports: [ScrambledHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ScrambledHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const header = el.querySelector('[data-testid="slot-header"]')!;
    const nav = el.querySelector('[data-testid="slot-nav"]')!;
    const content = el.querySelector('[data-testid="slot-content"]')!;
    const tabBar = el.querySelector('[data-testid="slot-tab-bar"]')!;

    const precedes = (a: Element, b: Element) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(precedes(header, nav)).toBe(true);
    expect(precedes(nav, content)).toBe(true);
    expect(precedes(content, tabBar)).toBe(true);
  });

  it('leaves no wrapper element behind for a slot with no projected content', async () => {
    @Component({
      selector: 'fleet-app-shell-partial-host',
      imports: [AppShell],
      template: `
        <fleet-app-shell>
          <div shell-header data-testid="slot-header">header</div>
          <div data-testid="slot-content">content</div>
        </fleet-app-shell>
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

    const shell = el.querySelector('.shell')!;
    // Only the two projected nodes should exist as children of `.shell` — a
    // slot with nothing projected into it must not leave any wrapper element
    // of its own behind (e.g. a `<div>` AppShell wraps each `ng-content` in),
    // not just an absent `data-testid`.
    expect(Array.from(shell.children).map((node) => node.getAttribute('data-testid'))).toEqual([
      'slot-header',
      'slot-content',
    ]);
  });
});
