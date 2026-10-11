import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { ProjectLensControl, lensChoices } from './project-lens-control';

@Component({ selector: 'app-test-lens-control-view', template: '' })
class TestView {}

const project = (slug: string, name: string) => ({
  project_id: `proj_${slug}`,
  slug,
  name,
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
});

describe('ProjectLensControl', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (_method, path) =>
      path === '/api/projects' ? { projects: [project('zeta', 'Zeta'), project('blizzard', 'Blizzard')] } : {},
    );
    await TestBed.configureTestingModule({
      imports: [ProjectLensControl],
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

  it('offers All, then every project by name', () => {
    expect(lensChoices([project('zeta', 'Zeta'), project('blizzard', 'Blizzard')])).toEqual([
      { slug: null, label: 'All' },
      { slug: 'blizzard', label: 'Blizzard' },
      { slug: 'zeta', label: 'Zeta' },
    ]);
  });

  it('marks the lens the URL is on, and switches the lens keeping the view', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/gardening/runs/chk_1');
    const fixture = TestBed.createComponent(ProjectLensControl);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const chip = (slug: string) => el.querySelector<HTMLElement>(`[data-testid="project-lens-${slug}"]`);

    expect(chip('all')?.getAttribute('aria-pressed')).toBe('true');
    expect(chip('blizzard')?.textContent).toContain('Blizzard');

    chip('blizzard')!.click();
    await settle(fixture);
    expect(router.url).toBe('/p/blizzard/gardening/runs');
    expect(chip('blizzard')?.getAttribute('aria-pressed')).toBe('true');

    chip('all')!.click();
    await settle(fixture);
    expect(router.url).toBe('/gardening/runs');
  });

  it('reads no project while the shell has not entered its tenant', async () => {
    const fixture = TestBed.createComponent(ProjectLensControl);
    fixture.componentRef.setInput('enabled', false);
    await settle(fixture);

    expect(stub.forRoute('/api/projects', 'GET').length).toBe(0);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="project-lens-all"]')).not.toBeNull();
  });
});
