import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ConnectionLight } from './connection-status.model';
import { StatusLight } from './status-light';

describe('StatusLight', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StatusLight],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function render(light: ConnectionLight): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(StatusLight);
    fixture.componentRef.setInput('light', light);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('draws a lit light as a beacon in its tone and blink', async () => {
    const el = await render({ kind: 'lit', tone: 'amber', blink: 'hold' });

    expect(el.querySelector('.hollow')).toBeNull();
    const beacon = el.querySelector('.beacon');
    expect(beacon?.classList.contains('active')).toBe(true);
    expect(beacon?.classList.contains('hold')).toBe(true);
    expect((beacon as HTMLElement).style.background).toBe('var(--amber)');
  });

  it('draws a blinking light with the step class', async () => {
    const el = await render({ kind: 'lit', tone: 'red', blink: 'step' });

    expect(el.querySelector('.beacon')?.classList.contains('step')).toBe(true);
  });

  it('draws a never-connected light as a hollow mark with no beacon', async () => {
    const el = await render({ kind: 'never' });

    expect(el.querySelector('.hollow')).not.toBeNull();
    expect(el.querySelector('fleet-kit-beacon')).toBeNull();
  });
});
