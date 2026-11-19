import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { Flags } from '../flags.domain';
import { FlagStore, type FlagsRead, provideFlags } from './flag-store';

@Component({
  selector: 'fleet-test-flag-host',
  template: `@if (flags.on('shiny')) {<p data-testid="shiny">shiny</p>}`,
})
class FlagHost {
  protected readonly flags = TestBed.inject(FlagStore);
}

describe('FlagStore', () => {
  let calls: number;

  const store = async (answer: Flags | Error) => {
    calls = 0;
    const read: FlagsRead = { queryKey: ['test', 'flags'], queryFn: async () => {
      calls++;
      if (answer instanceof Error) throw answer;
      return answer;
    } };
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideFlags(read),
      ],
    });
    const flags = TestBed.inject(FlagStore);
    await flags.whenSettled();
    return flags;
  };

  it('reads a flag the answer holds on as on, and one it holds off as off', async () => {
    const flags = await store({ flags: { shiny: true, dull: false }, overridden: ['shiny'] });

    expect(flags.on('shiny')).toBe(true);
    expect(flags.on('dull')).toBe(false);
    expect(flags.overridden()).toEqual(['shiny']);
  });

  it('reads a name the answer does not hold as off', async () => {
    const flags = await store({ flags: { shiny: true }, overridden: [] });

    expect(flags.on('unknown')).toBe(false);
    expect(flags.on('constructor')).toBe(false);
  });

  it('leaves every flag off and nothing overridden when the read fails', async () => {
    const flags = await store(new Error('down'));

    expect(flags.on('shiny')).toBe(false);
    expect(flags.overridden()).toEqual([]);
  });

  it('requests /api/flags once however many times it is asked', async () => {
    const flags = await store({ flags: { shiny: true }, overridden: [] });
    await flags.whenSettled();
    await flags.whenSettled();

    expect(calls).toBe(1);
  });

  it('shows a templated feature with the flag on and hides it with the flag off', async () => {
    for (const [shiny, shown] of [
      [true, true],
      [false, false],
    ] as const) {
      TestBed.resetTestingModule();
      await store({ flags: { shiny }, overridden: [] });
      const fixture = TestBed.createComponent(FlagHost);
      await fixture.whenStable();

      expect(fixture.nativeElement.querySelector('[data-testid="shiny"]') !== null).toBe(shown);
    }
  });
});
