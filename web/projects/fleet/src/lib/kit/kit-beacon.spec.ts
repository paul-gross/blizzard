import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { Tone } from './tone';
import { KitBeacon, type BeaconBlink, type BeaconTone } from './kit-beacon';

@Component({
  selector: 'fleet-test-host',
  imports: [KitBeacon],
  template: `<fleet-kit-beacon [active]="active()" [tone]="tone()" [blink]="blink()" />`,
})
class TestHost {
  readonly active = signal(false);
  readonly tone = signal<BeaconTone | Tone>('amber');
  readonly blink = signal<BeaconBlink>('pulse');
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

  it('takes the green tone', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set(true);
    fixture.componentInstance.tone.set('green');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect((el.querySelector('.beacon') as HTMLElement).getAttribute('style')).toContain('var(--green)');
  });

  it('pulses by default and steps when asked', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set(true);
    await fixture.whenStable();
    const beacon = (fixture.nativeElement as HTMLElement).querySelector('.beacon') as HTMLElement;
    expect(beacon.classList.contains('step')).toBe(false);

    fixture.componentInstance.blink.set('step');
    await fixture.whenStable();
    expect(beacon.classList.contains('step')).toBe(true);
  });

  it('shows its tone steady, without animating, when held', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set(true);
    fixture.componentInstance.tone.set('green');
    fixture.componentInstance.blink.set('hold');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const beacon = el.querySelector('.beacon') as HTMLElement;
    expect(beacon.classList.contains('hold')).toBe(true);
    expect(beacon.getAttribute('style')).toContain('var(--green)');
  });

  it('colors a derived-status Tone as toneColor does', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set(true);
    fixture.componentInstance.tone.set('waiting');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect((el.querySelector('.beacon') as HTMLElement).getAttribute('style')).toContain('var(--amber-hi)');
  });
});
