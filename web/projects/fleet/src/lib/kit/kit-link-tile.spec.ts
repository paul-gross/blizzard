import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitLinkTile } from './kit-link-tile';

@Component({
  selector: 'fleet-test-host',
  imports: [KitLinkTile],
  template: `<a fleetKitLinkTile href="/p/x" aria-label="Open X's garden"><span class="inner">facts</span></a>`,
})
class TestHost {}

describe('KitLinkTile', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('is one anchor wrapping the projected content, named by its label', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const anchors = el.querySelectorAll('a');
    expect(anchors.length).toBe(1);
    expect(anchors[0].getAttribute('href')).toBe('/p/x');
    expect(anchors[0].getAttribute('aria-label')).toBe("Open X's garden");
    expect(anchors[0].querySelector('.inner')?.textContent).toBe('facts');
  });

  it('is a focusable flex tile that stays the one tab stop', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    document.body.appendChild(fixture.nativeElement);
    try {
      const a = (fixture.nativeElement as HTMLElement).querySelector('a')!;
      a.focus();
      expect(document.activeElement).toBe(a);
      expect(getComputedStyle(a).display).toBe('flex');
      expect(a.tabIndex).toBe(0);
    } finally {
      (fixture.nativeElement as HTMLElement).remove();
    }
  });
});
