import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { flagsApiFlagsGet } from '../../api/hub';
import { client as hubClient } from '../../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../../testing/stub-request-client';
import { FlagStore, type FlagsRead, provideFlags } from './flag-store';

const READ: FlagsRead = {
  queryKey: ['test', 'flags'],
  queryFn: async () => {
    const { data, error } = await flagsApiFlagsGet({ throwOnError: false });
    if (error) throw error;
    return data!;
  },
};

@Component({
  selector: 'fleet-test-flag-host',
  template: `@if (flags.on('shiny')) {<p data-testid="shiny">shiny</p>}`,
})
class FlagHost {
  protected readonly flags = TestBed.inject(FlagStore);
}

describe('FlagStore', () => {
  let stub: RequestClientStub;

  const store = async (answer: unknown) => {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/flags' ? answer : {}));
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideFlags(READ),
      ],
    });
    const flags = TestBed.inject(FlagStore);
    await flags.whenSettled();
    return flags;
  };

  afterEach(() => stub.restore());

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
    const flags = await store(stubError(500, { detail: 'down' }));

    expect(flags.on('shiny')).toBe(false);
    expect(flags.overridden()).toEqual([]);
  });

  it('requests /api/flags once however many times it is asked', async () => {
    const flags = await store({ flags: { shiny: true }, overridden: [] });
    await flags.whenSettled();
    await flags.whenSettled();

    expect(stub.forRoute('/api/flags')).toHaveLength(1);
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
      stub.restore();
    }
  });
});
