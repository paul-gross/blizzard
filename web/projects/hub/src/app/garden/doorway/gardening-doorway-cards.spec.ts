import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { GardeningDoorwayCards } from './gardening-doorway-cards';
import type { DoorwayCard } from './gardening-doorway.model';

const card: DoorwayCard = {
  projectId: 'proj_b', slug: 'blizzard', name: 'Blizzard', color: '#123456',
  href: '/p/blizzard/gardening/proposals', counts: '1 routine · 0 pending proposals',
};

describe('GardeningDoorwayCards', () => {
  it('renders a link and emits only for an unmodified primary click', async () => {
    TestBed.configureTestingModule({ imports: [GardeningDoorwayCards], providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(GardeningDoorwayCards);
    fixture.componentRef.setInput('cards', [card]);
    await fixture.whenStable();
    const selected: string[] = [];
    fixture.componentInstance.projectSelected.subscribe((slug) => selected.push(slug));
    const link = (fixture.nativeElement as HTMLElement).querySelector<HTMLAnchorElement>('a')!;
    expect(link.getAttribute('href')).toBe(card.href);
    expect(link.textContent).toContain(card.counts);
    expect(link.querySelector('fleet-kit-swatch')).not.toBeNull();
    const modified = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true });
    link.dispatchEvent(modified);
    expect(modified.defaultPrevented).toBe(false);
    const primary = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(primary);
    expect(primary.defaultPrevented).toBe(true);
    expect(selected).toEqual(['blizzard']);
  });
});
