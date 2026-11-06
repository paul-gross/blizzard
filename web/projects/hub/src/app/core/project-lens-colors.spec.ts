import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { ProjectView } from 'fleet';
import { projectView, type RequestClientStub, settle, stubHubClient } from 'fleet/testing';
import { provideProjectLens } from './project-lens';
import { injectShowProjectColors, showsProjectColors } from './project-lens-colors';

describe('showsProjectColors', () => {
  it('paints project colours on All with more than one project', () => {
    expect(showsProjectColors(null, 2)).toBe(true);
    expect(showsProjectColors(null, 7)).toBe(true);
  });

  it('drops them under a one-project lens, whatever the tenant holds', () => {
    expect(showsProjectColors('glacier', 2)).toBe(false);
    expect(showsProjectColors('glacier', 1)).toBe(false);
  });

  it('drops them on a tenant with one project, or none read yet', () => {
    expect(showsProjectColors(null, 1)).toBe(false);
    expect(showsProjectColors(null, 0)).toBe(false);
  });
});

@Component({ selector: 'app-test-colors-view', template: '' })
class TestView {}

@Component({ selector: 'app-test-colors-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class ColorsHost {
  readonly show = injectShowProjectColors();
}

describe('injectShowProjectColors', () => {
  let stub: RequestClientStub;
  let projects: ProjectView[];

  beforeEach(async () => {
    stub = stubHubClient({ 'GET /api/projects': () => ({ projects }) });
    await TestBed.configureTestingModule({
      imports: [ColorsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
        provideProjectLens(),
      ],
    }).compileComponents();
  });
  afterEach(() => stub.restore());

  async function showAt(url: string, count: number): Promise<boolean> {
    projects = Array.from({ length: count }, (_, i) => projectView({ slug: `p${i}`, project_id: `proj_${i}`, name: `P${i}` }));
    await TestBed.inject(Router).navigateByUrl(url);
    const fixture = TestBed.createComponent(ColorsHost);
    await settle(fixture);
    return fixture.componentInstance.show();
  }

  it('shows them on All over a tenant of several projects', async () => {
    expect(await showAt('/board', 2)).toBe(true);
  });

  it('hides them under a lens', async () => {
    expect(await showAt('/p/p0/board', 2)).toBe(false);
  });

  it('hides them on a one-project tenant', async () => {
    expect(await showAt('/board', 1)).toBe(false);
  });
});
