import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { AppShell } from './app-shell';

/** A host projecting one marker element per slot, each with a distinct
 * `data-testid` so the rendered order is provable regardless of which
 * selector matched it. */
@Component({
  selector: 'fleet-app-shell-test-host',
  imports: [AppShell],
  template: `
    <fleet-app-shell>
      <div shell-header data-testid="slot-header">header</div>
      <div shell-nav data-testid="slot-nav">nav</div>
      <div data-testid="slot-content">content</div>
      <div shell-tab-bar data-testid="slot-tab-bar">tab-bar</div>
    </fleet-app-shell>
  `,
})
class AppShellTestHost {}

describe('AppShell', () => {
  it('orders the projected slots header, nav, content, tab-bar regardless of markup order', async () => {
    await TestBed.configureTestingModule({
      imports: [AppShellTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(AppShellTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const shell = el.querySelector('.shell');
    expect(shell).not.toBeNull();

    const order = Array.from(shell!.querySelectorAll('[data-testid]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(order).toEqual(['slot-header', 'slot-nav', 'slot-content', 'slot-tab-bar']);
  });

  it('places the header node before the nav node in document order (compareDocumentPosition)', async () => {
    await TestBed.configureTestingModule({
      imports: [AppShellTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(AppShellTestHost);
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

  it('renders only the slots given content — an absent slot leaves no empty marker behind', async () => {
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

    expect(el.querySelector('[data-testid="slot-header"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="slot-content"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="slot-nav"]')).toBeNull();
    expect(el.querySelector('[data-testid="slot-tab-bar"]')).toBeNull();
  });
});
