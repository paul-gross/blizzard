import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { Flags } from '../flags.domain';
import { APP_SCOPE, type InAppLink } from '../app-scope';
import { flagGuard } from './flag-guard';
import { FlagStore, type FlagsRead, provideFlags } from './flag-store';

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
  const setup = async (
    answer: Flags | Error,
    lens: string | null = null,
  ) => {
    const read: FlagsRead = { queryKey: ['test', 'flags'], queryFn: async () => {
      if (answer instanceof Error) throw answer;
      return answer;
    } };
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideFlags(read),
        provideRouter([
          { path: 'shiny', canActivate: [flagGuard('shiny')], component: View },
          { path: 'p/:slug', children: [{ path: '**', component: View }] },
          { path: '**', component: View },
        ]),
        ...(lens === null
          ? []
          : [{ provide: APP_SCOPE, useValue: { path: (link: InAppLink) => `/p/${lens}${link === '/' ? '' : String(link)}`, query: () => ({ ignored: 'x' }) } }]),
      ],
    });
    return TestBed.inject(Router);
  };

  it('activates the route while the flag is on', async () => {
    const router = await setup({ flags: { shiny: true }, overridden: [] });

    await router.navigateByUrl('/shiny');

    expect(router.url).toBe('/shiny');
  });

  it('redirects to the app root while the flag is off, or unknown, or the read failed', async () => {
    const answers: (Flags | Error)[] = [
      { flags: { shiny: false }, overridden: [] },
      { flags: {}, overridden: [] },
      new Error('down'),
    ];
    for (const answer of answers) {
      TestBed.resetTestingModule();
      const router = await setup(answer);

      await router.navigateByUrl('/shiny');

      expect(router.url).toBe('/');
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
