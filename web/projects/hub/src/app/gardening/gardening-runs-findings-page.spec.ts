import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GardeningRunsFindingsPage } from './gardening-runs-findings-page';

describe('GardeningRunsFindingsPage', () => {
  it('renders its own empty state (blizzard#397 — sheet content is a later issue)', async () => {
    await TestBed.configureTestingModule({
      imports: [GardeningRunsFindingsPage],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunsFindingsPage);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-runs-findings-empty"]')?.textContent).toContain(
      'tending begins when there is growth worth pruning',
    );
  });
});
