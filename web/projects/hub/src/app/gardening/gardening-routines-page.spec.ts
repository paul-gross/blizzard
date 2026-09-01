import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GardeningRoutinesPage } from './gardening-routines-page';

describe('GardeningRoutinesPage', () => {
  it('renders its own empty state (blizzard#397 — sheet content is a later issue)', async () => {
    await TestBed.configureTestingModule({
      imports: [GardeningRoutinesPage],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRoutinesPage);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routines-empty"]')?.textContent).toContain(
      'tending begins when there is growth worth pruning',
    );
  });
});
