import { Component, input, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { LocalPauseControl } from './app-pause-control';
import { MachinePage } from './machine-page';
import { LocalSelftest } from './selftest';
import { LocalThrottle } from './throttle';

@Component({ selector: 'app-pause-control', template: '<span data-testid="pause-target">{{ runnerId() }}</span>' })
class PauseTarget {
  readonly runnerId = input.required<string>();
}

@Component({ selector: 'app-local-throttle', template: '<span data-testid="throttle-target">{{ runnerId() }}</span>' })
class ThrottleTarget {
  readonly runnerId = input.required<string>();
}

@Component({ selector: 'app-local-selftest', template: '<span data-testid="selftest-target">{{ runnerId() }}</span>' })
class SelftestTarget {
  readonly runnerId = input.required<string>();
}

describe('MachinePage', () => {
  it('retargets every runner control when the router reuses the page for a different id', async () => {
    await TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideRouter([{ path: 'machine/:runnerId', component: MachinePage }])],
    })
      .overrideComponent(MachinePage, {
        remove: { imports: [LocalPauseControl, LocalThrottle, LocalSelftest] },
        add: { imports: [PauseTarget, ThrottleTarget, SelftestTarget] },
      })
      .compileComponents();
    const harness = await RouterTestingHarness.create();
    const page = await harness.navigateByUrl('/machine/rn_a', MachinePage);
    expect(harness.routeNativeElement?.querySelector('[data-testid="pause-target"]')?.textContent).toBe('rn_a');
    expect(harness.routeNativeElement?.querySelector('[data-testid="throttle-target"]')?.textContent).toBe('rn_a');
    expect(harness.routeNativeElement?.querySelector('[data-testid="selftest-target"]')?.textContent).toBe('rn_a');

    const reused = await harness.navigateByUrl('/machine/rn_b', MachinePage);
    expect(reused).toBe(page);
    expect(harness.routeNativeElement?.querySelector('h1')?.textContent).toContain('rn_b');
    expect(harness.routeNativeElement?.querySelector('[data-testid="pause-target"]')?.textContent).toBe('rn_b');
    expect(harness.routeNativeElement?.querySelector('[data-testid="throttle-target"]')?.textContent).toBe('rn_b');
    expect(harness.routeNativeElement?.querySelector('[data-testid="selftest-target"]')?.textContent).toBe('rn_b');
  });
});
