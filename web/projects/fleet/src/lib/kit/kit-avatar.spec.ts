import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitAvatar } from './kit-avatar';

describe('KitAvatar', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [KitAvatar],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a decorative person-glyph svg', async () => {
    const fixture = TestBed.createComponent(KitAvatar);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const svg = el.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });
});
