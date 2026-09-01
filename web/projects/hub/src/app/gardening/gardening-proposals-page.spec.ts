import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GardeningProposalsPage } from './gardening-proposals-page';

describe('GardeningProposalsPage', () => {
  it('renders its own empty state (blizzard#397 — the docket itself is a later issue)', async () => {
    await TestBed.configureTestingModule({
      imports: [GardeningProposalsPage],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningProposalsPage);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')?.textContent).toContain(
      'tending begins when there is growth worth pruning',
    );
  });
});
