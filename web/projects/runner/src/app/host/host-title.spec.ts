import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { HostRegistrationSummary } from './host-registration.model';
import { HostTitle } from './host-title';

const REGISTERED: HostRegistrationSummary = {
  state: 'registered',
  label: 'registered',
  tone: 'running',
  lines: [],
  detail: null,
  answeredAt: '2026-07-16T11:59:26.000Z',
};

describe('HostTitle', () => {
  async function render(registration: HostRegistrationSummary | null): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [HostTitle],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(HostTitle);
    fixture.componentRef.setInput('name', 'xps');
    fixture.componentRef.setInput('hostId', 'hst_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    fixture.componentRef.setInput('hostRef', 'N3YJ9');
    fixture.componentRef.setInput('registration', registration);
    fixture.componentRef.setInput('tone', 'green');
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('names the host, its ref, its registration and when the hub answered', async () => {
    const el = await render(REGISTERED);

    expect(el.querySelector('[data-testid="host-name"]')?.textContent?.trim()).toBe('xps');
    expect(el.querySelector('.ref')?.getAttribute('title')).toBe('hst_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(el.querySelector('[data-testid="host-registration-state"]')?.textContent?.trim()).toBe('registered');
    expect(el.querySelector('[data-testid="host-registration-answered"]')).not.toBeNull();
    expect(el.querySelector('fleet-kit-beacon')).not.toBeNull();
  });

  it('spells out the hub\'s sentence on a refusal, naming what to upgrade', async () => {
    const detail = "runner 0.1.4 is older than this hub's oldest supported line, 0.2; upgrade the runner host to 0.2 or later, up to 0.3";
    const el = await render({ ...REGISTERED, state: 'version_refused', label: 'version refused', tone: 'needs', lines: ['host hs_1', detail], detail });

    expect(el.querySelector('[data-testid="host-registration-state"]')?.textContent?.trim()).toBe('version refused');
    expect(el.querySelector('[data-testid="host-registration-detail"]')?.textContent?.trim()).toBe(detail);
  });

  it('shows no detail line while the hub gave no sentence', async () => {
    const el = await render(REGISTERED);

    expect(el.querySelector('[data-testid="host-registration-detail"]')).toBeNull();
  });

  it('shows neither light nor registration before the registration reports', async () => {
    const el = await render(null);

    expect(el.querySelector('[data-testid="host-name"]')?.textContent?.trim()).toBe('xps');
    expect(el.querySelector('[data-testid="host-registration-state"]')).toBeNull();
    expect(el.querySelector('fleet-kit-beacon')).toBeNull();
  });
});
