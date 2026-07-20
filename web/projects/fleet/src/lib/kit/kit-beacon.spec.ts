import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitBeacon, type BeaconTone } from './kit-beacon';

@Component({
  selector: 'fleet-test-host',
  imports: [KitBeacon],
  template: `<fleet-kit-beacon [active]="active()" [tone]="tone()" />`,
})
class TestHost {
  readonly active = signal(false);
  readonly tone = signal<BeaconTone>('amber');
}

describe('KitBeacon', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('sits static grey and does not throb when inactive', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const beacon = el.querySelector('.beacon') as HTMLElement;
    expect(beacon.classList.contains('active')).toBe(false);
    expect(beacon.getAttribute('style')).toContain('var(--label-dim)');
  });

  it('throbs in the given tone color when active', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set(true);
    fixture.componentInstance.tone.set('red');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const beacon = el.querySelector('.beacon') as HTMLElement;
    expect(beacon.classList.contains('active')).toBe(true);
    expect(beacon.getAttribute('style')).toContain('var(--red)');
  });

  it('defaults to amber when active with no tone specified', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect((el.querySelector('.beacon') as HTMLElement).getAttribute('style')).toContain('var(--amber)');
  });
});
