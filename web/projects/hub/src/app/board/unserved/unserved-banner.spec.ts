import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { UnservedBanner } from './unserved-banner';

@Component({ selector: 'app-test-banner-view', template: '' })
class TestView {}

const project = (slug: string, name: string) => ({
  project_id: `proj_${slug}`,
  slug,
  name,
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
});

const serving = (slug: string) => ({
  runner_id: `rn_${slug}`,
  runner_name: slug,
  online: true,
  retired: false,
  projects: [{ entry: slug, project_id: `proj_${slug}`, slug, name: null, claimable: true }],
});

describe('UnservedBanner', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/projects') return { projects: [project('alpha', 'Alpha'), project('beta', 'Beta')] };
      if (path === '/api/runners') return { runners: [serving('alpha')] };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [UnservedBanner],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  async function bannerAt(url: string): Promise<HTMLElement | null> {
    await TestBed.inject(Router).navigateByUrl(url);
    const fixture = TestBed.createComponent(UnservedBanner);
    await settle(fixture);
    return (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="unserved-banner"]');
  }

  it('marks a lensed project no live runner serves', async () => {
    const banner = await bannerAt('/p/beta/board');

    expect(banner?.textContent).toContain('No live runner serves Beta');
  });

  it('shows nothing under the lens of a project a live runner serves', async () => {
    expect(await bannerAt('/p/alpha/board')).toBeNull();
  });

  it('shows nothing on All, and reads no project there', async () => {
    expect(await bannerAt('/board')).toBeNull();
    expect(stub.forRoute('/api/projects', 'GET')).toHaveLength(0);
  });
});
