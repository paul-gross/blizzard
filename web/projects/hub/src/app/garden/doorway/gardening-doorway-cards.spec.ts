import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { GardeningDoorwayCards } from './gardening-doorway-cards';
import type { DoorwayCard } from './gardening-doorway.model';

const card = (fields: Partial<DoorwayCard> = {}): DoorwayCard => ({
  projectId: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  link: ['/p', 'blizzard', 'gardening', 'proposals'],
  routines: ['garden-weekly'],
  scopes: 2,
  inProgress: 1,
  findings: 5,
  lastRun: '2026-03-05T00:00:00Z',
  waiting: 1,
  unplanted: false,
  ...fields,
});

async function render(cards: readonly DoorwayCard[]): Promise<HTMLElement> {
  TestBed.configureTestingModule({
    imports: [GardeningDoorwayCards],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  });
  const fixture = TestBed.createComponent(GardeningDoorwayCards);
  fixture.componentRef.setInput('cards', cards);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('GardeningDoorwayCards', () => {
  it('renders a card with its standing and the way in', async () => {
    const el = await render([card()]);

    const open = el.querySelector<HTMLAnchorElement>('[data-testid="gardening-doorway-card-blizzard"]')!;
    expect(open.tagName).toBe('A');
    expect(open.getAttribute('href')).toBe('/p/blizzard/gardening/proposals');
    expect(open.getAttribute('aria-label')).toBe("Open Blizzard's garden");
    expect(open.querySelector('a'), 'no nested link inside the tile').toBeNull();
    expect(open.textContent).toContain('garden-weekly');
    expect(open.querySelector('[data-testid="gardening-doorway-waiting"]')?.textContent).toContain('1 proposal waiting');
    expect(open.querySelector('[data-testid="gardening-doorway-sweeping-blizzard"]')?.textContent).toContain('1 run in progress');
    expect(open.querySelector('[data-testid="gardening-doorway-findings-blizzard"]')?.textContent).toContain('5 from the last runs');
  });

  it('shows the last run only where one finished', async () => {
    const el = await render([card(), card({ slug: 'winter', name: 'Winter', projectId: 'proj_w', lastRun: null })]);

    expect(el.querySelector('[data-testid="gardening-doorway-last-run-blizzard"]')?.textContent).toContain('last run');
    expect(el.querySelector('[data-testid="gardening-doorway-last-run-winter"]')).toBeNull();
  });

  it('says an unplanted project has no gardening set up, with no facts and no waiting badge', async () => {
    const el = await render([card({ slug: 'winter', name: 'Winter', projectId: 'proj_w', routines: [], scopes: 0, inProgress: 0, findings: 0, lastRun: null, waiting: 0, unplanted: true })]);

    const winter = el.querySelector('[data-testid="gardening-doorway-card-winter"]')!;
    expect(winter.textContent).toContain("Gardening isn't set up for this project.");
    expect(winter.querySelector('[data-testid="gardening-doorway-findings-winter"]')).toBeNull();
    expect(winter.querySelector('[data-testid="gardening-doorway-waiting"]')).toBeNull();
  });
});
