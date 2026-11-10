import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { settle } from 'fleet/testing';

import { GardeningBreadcrumbView } from './gardening-breadcrumb-view';

describe('GardeningBreadcrumbView', () => {
  async function render() {
    await TestBed.configureTestingModule({
      imports: [GardeningBreadcrumbView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningBreadcrumbView);
    fixture.componentRef.setInput('doorwayHref', '/gardening');
    fixture.componentRef.setInput('projectLabel', 'Blizzard');
    fixture.componentRef.setInput('checkedSlug', 'blizzard');
    fixture.componentRef.setInput('surfaceLabel', 'Scopes');
    fixture.componentRef.setInput('choices', [
      { slug: 'winter', label: 'Winter', waiting: 0 },
      { slug: 'blizzard', label: 'Blizzard', waiting: 1 },
      { slug: null, label: 'All projects', waiting: null },
    ]);
    await settle(fixture);
    return fixture;
  }

  it('exposes a real doorway anchor and forwards its original click', async () => {
    const fixture = await render();
    const link = fixture.nativeElement.querySelector('[data-testid="gardening-crumb-doorway"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/gardening');
    let emitted: MouseEvent | undefined;
    fixture.componentInstance.doorwayClick.subscribe((event) => { emitted = event; event.preventDefault(); });
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(event);
    expect(emitted).toBe(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('renders choices in supplied order, checked state and nullable counts, and emits selection', async () => {
    const fixture = await render();
    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);
    const panel = document.querySelector('[data-testid="gardening-crumb-project-panel"]')!;
    const choices = Array.from(panel.querySelectorAll('[role="menuitemradio"]'));
    expect(choices.map((item) => item.getAttribute('data-testid'))).toEqual([
      'gardening-crumb-project-winter', 'gardening-crumb-project-blizzard', 'gardening-crumb-project-all',
    ]);
    expect(choices[1].getAttribute('aria-checked')).toBe('true');
    expect(panel.querySelector('[data-testid="gardening-crumb-waiting-winter"]')?.textContent).toContain('0 proposals waiting');
    expect(panel.querySelector('[data-testid="gardening-crumb-waiting-blizzard"]')?.textContent).toContain('1 proposal waiting');
    expect(panel.querySelector('[data-testid="gardening-crumb-waiting-null"]')).toBeNull();
    let selected: string | null | undefined;
    fixture.componentInstance.projectSelected.subscribe((slug) => { selected = slug; });
    (choices[2] as HTMLElement).click();
    expect(selected).toBeNull();
  });

  it('omits an absent surface and keeps unread counts hidden', async () => {
    const fixture = await render();
    fixture.componentRef.setInput('surfaceLabel', null);
    fixture.componentRef.setInput('choices', [{ slug: 'blizzard', label: 'Blizzard', waiting: null }]);
    await settle(fixture);
    expect(fixture.nativeElement.querySelector('[data-testid="gardening-crumb-surface"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('nav').getAttribute('aria-label')).toBe('Breadcrumb');
    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);
    expect(document.querySelector('[data-testid="gardening-crumb-project-head"]')?.textContent).toContain('this surface');
    expect(document.querySelector('[data-testid="gardening-crumb-waiting-blizzard"]')).toBeNull();
  });
});
