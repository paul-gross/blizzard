import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitAccordionSection } from './kit-accordion-section';

@Component({
  imports: [KitAccordionSection],
  template: `
    <fleet-kit-accordion-section sectionId="test-section" [expanded]="expanded" (expandedChange)="expanded = $event">
      <span accordionHeader data-testid="header-content">Transcripts</span>
      <p data-testid="body-content">body</p>
    </fleet-kit-accordion-section>
  `,
})
class Host {
  expanded = false;
}

describe('KitAccordionSection', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the projected header but not the body while collapsed', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="header-content"]')?.textContent).toContain('Transcripts');
    expect(el.querySelector('[data-testid="body-content"]')).toBeNull();
    expect(el.querySelector('[data-testid="accordion-section-head"]')?.getAttribute('aria-expanded')).toBe('false');
  });

  it('emits the next open state and renders the body once the consumer expands it', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="accordion-section-head"]')?.click();
    await fixture.whenStable();

    expect(fixture.componentInstance.expanded).toBe(true);
    expect(el.querySelector('[data-testid="body-content"]')?.textContent).toBe('body');
    expect(el.querySelector('[data-testid="accordion-section-head"]')?.getAttribute('aria-expanded')).toBe('true');
  });
});
