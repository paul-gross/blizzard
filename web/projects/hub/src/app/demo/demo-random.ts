import { InjectionToken } from '@angular/core';

export const DEMO_RANDOM = new InjectionToken<() => number>('DEMO_RANDOM', {
  providedIn: 'root',
  factory: () => Math.random,
});
