import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitPanel, KitPanelHeader } from './kit-panel';

@Component({
  selector: 'fleet-test-host',
  imports: [KitPanel, KitPanelHeader],
  template: `
    <fleet-kit-panel
      [label]="label()"
      [count]="count()"
      [countTestid]="'the-count'"
      [accent]="accent()"
      [bodyScroll]="bodyScroll()"
    >
      @if (withHeaderExtra()) {
        <span fleetKitPanelHeader data-testid="extra-header">extra</span>
      }
      <p data-testid="body-content">body</p>
    </fleet-kit-panel>
  `,
})
class TestHost {
  readonly label = signal<string | null>('Runners · fleet registry');
  readonly count = signal<number | string | null>(null);
  readonly withHeaderExtra = signal(false);
  readonly accent = signal<string | null>(null);
  readonly bodyScroll = signal(true);
}

describe('KitPanel', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the label and projects the body content', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.lbl')?.textContent).toContain('Runners · fleet registry');
    expect(el.querySelector('[data-testid="body-content"]')?.textContent).toBe('body');
  });

  it('omits the count span when count is null, empty, or undefined', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('.lbl')).toHaveLength(1);
  });

  it('renders a provided count as a second header label', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.count.set(4);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const labels = el.querySelectorAll('.lbl');
    expect(labels).toHaveLength(2);
    expect(labels[1].textContent).toContain('4');
    expect(el.querySelector('[data-testid="the-count"]')?.textContent).toContain('4');
  });

  it('projects extra header content alongside the label', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.withHeaderExtra.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="extra-header"]')?.textContent).toBe('extra');
    // Supplement mode: the label owns the bar, so the slot stays a bare
    // `display: contents` passthrough sizing to its own content.
    expect(el.querySelector('.hdr-slot')?.classList.contains('hdr-slot--owned')).toBe(false);
  });

  it('renders no header bar at all when nothing is set and nothing is projected', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.label.set(null);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('.p-hdr')).toHaveLength(0);
    expect(el.querySelector('[data-testid="body-content"]')?.textContent).toBe('body');
  });

  it('gives the slot the whole bar when it is the only header content (owns-the-bar)', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.label.set(null);
    fixture.componentInstance.withHeaderExtra.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('.p-hdr')).toHaveLength(1);
    expect(el.querySelectorAll('.lbl')).toHaveLength(0);
    expect(el.querySelector('.hdr-slot')?.classList.contains('hdr-slot--owned')).toBe(true);
  });

  it('follows the slot into and back out of occupancy, with no second declaration to keep in sync', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.label.set(null);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelectorAll('.p-hdr')).toHaveLength(0);

    fixture.componentInstance.withHeaderExtra.set(true);
    await fixture.whenStable();
    expect(el.querySelectorAll('.p-hdr')).toHaveLength(1);

    fixture.componentInstance.withHeaderExtra.set(false);
    await fixture.whenStable();
    expect(el.querySelectorAll('.p-hdr')).toHaveLength(0);
  });

  it('leaves the label and count uncolored when accent is unset', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.count.set(4);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const labels = el.querySelectorAll('.lbl');
    expect((labels[0] as HTMLElement).style.color).toBe('');
    expect(labels[1].classList.contains('cnt-accent')).toBe(false);
  });

  it('colors the label and flips the count to the accent look when set', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.count.set(2);
    fixture.componentInstance.accent.set('var(--red)');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const labels = el.querySelectorAll('.lbl');
    expect((labels[0] as HTMLElement).style.color).toBe('var(--red)');
    expect(labels[1].classList.contains('cnt-accent')).toBe(true);
  });

  it("leaves .p-body scrolling by default — every existing consumer's behavior", async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.p-body')?.classList.contains('p-body--noscroll')).toBe(false);
  });

  it('suppresses .p-body scrolling when bodyScroll is false (issue #309)', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.bodyScroll.set(false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.p-body')?.classList.contains('p-body--noscroll')).toBe(true);
  });
});
