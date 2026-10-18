import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { flagsApiFlagsGet } from '../../api/hub';
import { client as hubClient } from '../../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../../testing/stub-request-client';
import { IN_APP_LINK } from '../lens-link';
import { flagGuard } from './flag-guard';
import { FlagStore, type FlagsRead, provideFlags } from './flag-store';

const READ: FlagsRead = {
  queryKey: ['test', 'flags'],
  queryFn: async () => {
    const { data, error } = await flagsApiFlagsGet({ throwOnError: false });
    if (error) throw error;
    return data!;
  },
};

@Component({ selector: 'fleet-test-view', template: '' })
class View {}

/** A spec host nav: an entry for the guarded route, gated on the same flag. */
@Component({
  selector: 'fleet-test-nav',
  template: `@if (flags.on('shiny')) {<a data-testid="nav-shiny">Shiny</a>}`,
})
class Nav {
  protected readonly flags = TestBed.inject(FlagStore);
}

describe('flagGuard', () => {
  let stub: RequestClientStub;

  const setup = async (answer: unknown, lens: string | null = null) => {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/flags' ? answer : {}));
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideFlags(READ),
        provideRouter([
          { path: 'shiny', canActivate: [flagGuard('shiny')], component: View },
          { path: 'p/:slug', children: [{ path: '**', component: View }] },
          { path: '**', component: View },
        ]),
        ...(lens === null
          ? []
          : [{ provide: IN_APP_LINK, useValue: (link: string) => `/p/${lens}${link === '/' ? '' : link}` }]),
      ],
    });
    return TestBed.inject(Router);
  };

  afterEach(() => stub.restore());

  it('activates the route while the flag is on', async () => {
    const router = await setup({ flags: { shiny: true }, overridden: [] });

    await router.navigateByUrl('/shiny');

    expect(router.url).toBe('/shiny');
  });

  it('redirects to the app root while the flag is off, or unknown, or the read failed', async () => {
    for (const answer of [{ flags: { shiny: false }, overridden: [] }, { flags: {}, overridden: [] }, stubError(500, {})]) {
      TestBed.resetTestingModule();
      const router = await setup(answer);

      await router.navigateByUrl('/shiny');

      expect(router.url).toBe('/');
      stub.restore();
    }
  });

  it('keeps the lens the app’s link seam names when it redirects', async () => {
    const router = await setup({ flags: { shiny: false }, overridden: [] }, 'blizzard');

    await router.navigateByUrl('/shiny');

    expect(router.url).toBe('/p/blizzard');
  });

  it('hides a nav entry gated on the same name while the flag is off', async () => {
    await setup({ flags: { shiny: false }, overridden: [] });
    await TestBed.inject(FlagStore).whenSettled();
    const fixture = TestBed.createComponent(Nav);
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('[data-testid="nav-shiny"]')).toBeNull();
  });
});
