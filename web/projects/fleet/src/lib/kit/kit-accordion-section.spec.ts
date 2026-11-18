import {
  Component,
  provideZonelessChangeDetection,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitAccordionGroup } from './kit-accordion-group';
import { KitAccordionSection } from './kit-accordion-section';

@Component({
  imports: [KitAccordionGroup, KitAccordionSection],
  template: `
    <div fleetKitAccordionGroup>
      <fleet-kit-accordion-section
        sectionId="test-section"
        [disabled]="disabled"
        [expanded]="expanded()"
        (expandedChange)="expanded.set($event); changes.push($event)"
      >
        <span accordionHeader data-testid="header-content">Transcripts</span>
        <a accordionAside href="#" data-testid="aside-link">#1</a>
        <p data-testid="body-content">body</p>
      </fleet-kit-accordion-section>
    </div>
  `,
})
class Host {
  readonly expanded = signal(false);
  disabled = false;
  readonly changes: boolean[] = [];
}

/** Aria creates a panel's content and publishes its attributes in an after-render effect, one render after the state changes. */
const settle = (fixture: { whenStable(): Promise<unknown> }) =>
  new Promise<void>((done) =>
    setTimeout(() => fixture.whenStable().then(() => done()), 0),
  );

describe('KitAccordionSection', () => {
  async function mount(setup?: (host: Host) => void) {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    setup?.(fixture.componentInstance);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const head = () =>
      el.querySelector<HTMLButtonElement>(
        '[data-testid="accordion-section-head"]',
      )!;
    const body = () =>
      el.querySelector<HTMLElement>('[data-testid="accordion-section-body"]')!;
    return { fixture, el, head, body };
  }

  it('renders the projected header but not the body content while collapsed', async () => {
    const { el, head, body } = await mount();

    expect(
      el.querySelector('[data-testid="header-content"]')?.textContent,
    ).toContain('Transcripts');
    expect(el.querySelector('[data-testid="body-content"]')).toBeNull();
    expect(head().getAttribute('aria-expanded')).toBe('false');
    expect(body().style.display).toBe('none');
  });

  it('emits the next open state and renders the body once the consumer expands it', async () => {
    const { fixture, el, head, body } = await mount();

    head().click();
    await settle(fixture);

    expect(fixture.componentInstance.changes).toEqual([true]);
    expect(el.querySelector('[data-testid="body-content"]')?.textContent).toBe(
      'body',
    );
    expect(head().getAttribute('aria-expanded')).toBe('true');
    expect(body().style.display).toBe('');
  });

  it('renders an initially-expanded section open', async () => {
    const { el, head } = await mount((host) => host.expanded.set(true));

    expect(head().getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelector('[data-testid="body-content"]')).not.toBeNull();
  });

  it('pairs the trigger and the panel through their ids', async () => {
    const { head, body } = await mount();

    expect(head().id).toBe('test-section-head');
    expect(body().id).toBe('test-section-body');
    expect(head().getAttribute('aria-controls')).toBe(body().id);
    expect(body().getAttribute('aria-labelledby')).toBe(head().id);
    expect(body().getAttribute('role')).toBe('region');
  });

  it('emits nothing when the consumer pushes the open state in', async () => {
    const { fixture, head } = await mount();

    fixture.componentInstance.expanded.set(true);
    await settle(fixture);

    expect(head().getAttribute('aria-expanded')).toBe('true');
    expect(fixture.componentInstance.changes).toEqual([]);
  });

  it('does not toggle while disabled', async () => {
    const { fixture, head } = await mount((host) => (host.disabled = true));

    expect(head().getAttribute('aria-disabled')).toBe('true');
    head().click();
    await settle(fixture);

    expect(fixture.componentInstance.changes).toEqual([]);
    expect(head().getAttribute('aria-expanded')).toBe('false');
  });

  it('keeps the aside outside the trigger', async () => {
    const { el, head } = await mount();

    const aside = el.querySelector('[data-testid="aside-link"]')!;
    expect(head().contains(aside)).toBe(false);
  });
});
