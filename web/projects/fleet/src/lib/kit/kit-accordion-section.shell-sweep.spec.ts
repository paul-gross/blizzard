import {
  Component,
  provideZonelessChangeDetection,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { userEvent } from 'vitest/browser';

import { KitAccordionGroup } from './kit-accordion-group';
import { KitAccordionSection } from './kit-accordion-section';

@Component({
  imports: [KitAccordionGroup, KitAccordionSection],
  template: `
    <button data-testid="before" type="button">before</button>
    <section fleetKitAccordionGroup>
      <fleet-kit-accordion-section sectionId="a" [(expanded)]="a">
        <span accordionHeader>A</span>
        <p data-testid="body-a">A body</p>
      </fleet-kit-accordion-section>
      <fleet-kit-accordion-section sectionId="b" [(expanded)]="b">
        <span accordionHeader>B</span>
        <p data-testid="body-b">B body</p>
      </fleet-kit-accordion-section>
      <fleet-kit-accordion-section
        sectionId="c"
        [disabled]="true"
        [(expanded)]="c"
      >
        <span accordionHeader>C</span>
        <p data-testid="body-c">C body</p>
      </fleet-kit-accordion-section>
    </section>
    <button data-testid="after" type="button">after</button>
  `,
})
class Host {
  readonly a = signal(true);
  readonly b = signal(false);
  readonly c = signal(false);
}

describe('KitAccordionSection keyboard model shell sweep', () => {
  async function mount() {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    return { fixture, root };
  }
  const heads = (root: HTMLElement) =>
    Array.from(
      root.querySelectorAll<HTMLElement>(
        '[data-testid="accordion-section-head"]',
      ),
    );
  const q = (root: HTMLElement, testid: string) =>
    root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
  const settle = (fixture: { whenStable(): Promise<unknown> }) =>
    new Promise<void>((done) =>
      setTimeout(() => fixture.whenStable().then(() => done()), 50),
    );
  const panelShown = (root: HTMLElement, key: string) =>
    getComputedStyle(root.querySelector<HTMLElement>(`#${key}-body`)!)
      .display !== 'none' &&
    root.querySelector(`[data-testid="body-${key}"]`) !== null;

  it('reaches headers by Tab and moves between them with the arrow keys, Home and End', async () => {
    const { fixture, root } = await mount();
    try {
      q(root, 'before').focus();
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(heads(root)[0]);

      heads(root)[0].focus();
      for (const [key, index] of [
        ['ArrowDown', 1],
        ['ArrowDown', 2],
        ['Home', 0],
        ['End', 2],
        ['ArrowUp', 1],
      ] as const) {
        await userEvent.keyboard(`{${key}}`);
        expect(document.activeElement).toBe(heads(root)[index]);
      }
    } finally {
      fixture.destroy();
      root.remove();
    }
  });

  it('toggles with Enter and Space, and the panel follows; a disabled header is reached but stays shut', async () => {
    const { fixture, root } = await mount();
    try {
      expect(panelShown(root, 'a')).toBe(true);
      expect(panelShown(root, 'b')).toBe(false);

      heads(root)[1].focus();
      await userEvent.keyboard('{Enter}');
      await settle(fixture);
      expect(fixture.componentInstance.b()).toBe(true);
      expect(panelShown(root, 'b')).toBe(true);

      await userEvent.keyboard(' ');
      await settle(fixture);
      expect(fixture.componentInstance.b()).toBe(false);
      expect(panelShown(root, 'b')).toBe(false);

      await userEvent.keyboard('{ArrowDown}');
      expect(document.activeElement).toBe(heads(root)[2]);
      await userEvent.keyboard('{Enter}');
      await settle(fixture);
      expect(fixture.componentInstance.c()).toBe(false);
      expect(panelShown(root, 'c')).toBe(false);
    } finally {
      fixture.destroy();
      root.remove();
    }
  });

  it('pairs each trigger with its panel', async () => {
    const { root, fixture } = await mount();
    try {
      for (const head of heads(root)) {
        const panel = root.querySelector<HTMLElement>(
          `#${head.getAttribute('aria-controls')}`,
        )!;
        expect(panel.getAttribute('aria-labelledby')).toBe(head.id);
      }
    } finally {
      fixture.destroy();
      root.remove();
    }
  });
});
