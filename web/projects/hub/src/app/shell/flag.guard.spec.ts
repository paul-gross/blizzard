import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type FlagsResponse, flagGuard, provideFlags } from 'fleet/shell';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';

import { hubFlagsRead } from '../core/flags.query';
import { provideProjectLens } from '../core/project-lens';

@Component({ selector: 'app-test-flagged-view', template: '' })
class TestView {}

describe('the flag guard on the hub', () => {
  let stub: RequestClientStub;

  /** Opens `url` after landing on `from`, the way a person moves inside the board: the lens is the page they are on. */
  async function open(url: string, answer: FlagsResponse, from = '/') {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/flags' ? answer : {}));
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideFlags(hubFlagsRead),
        provideProjectLens(),
        provideRouter([
          { path: 'shiny', canActivate: [flagGuard('shiny')], component: TestView },
          {
            path: 'p/:projectSlug',
            children: [
              { path: 'shiny', canActivate: [flagGuard('shiny')], component: TestView },
              { path: '', component: TestView },
            ],
          },
          { path: '', component: TestView },
        ]),
      ],
    });
    const router = TestBed.inject(Router);
    await router.navigateByUrl(from);
    await router.navigateByUrl(url);
    return router;
  }

  afterEach(() => stub.restore());

  it('opens a route whose flag is on, under the lens too', async () => {
    const on = { flags: { shiny: true }, overridden: [] };

    expect((await open('/shiny', on)).url).toBe('/shiny');
    stub.restore();
    TestBed.resetTestingModule();
    expect((await open('/p/blizzard/shiny', on)).url).toBe('/p/blizzard/shiny');
  });

  it('sends a route whose flag is off to the board’s root', async () => {
    expect((await open('/shiny', { flags: { shiny: false }, overridden: [] })).url).toBe('/');
  });

  it('keeps the project lens the board is on when it sends a lensed route away', async () => {
    expect((await open('/p/blizzard/shiny', { flags: {}, overridden: [] }, '/p/blizzard')).url).toBe('/p/blizzard');
  });
});
