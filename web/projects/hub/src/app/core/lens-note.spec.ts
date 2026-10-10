import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type ProjectView, hubProjectsKey } from 'fleet';

import { LensNote } from './lens-note';
import { ProjectLens } from './project-lens';

const WINTER: ProjectView = {
  project_id: 'proj_w',
  slug: 'winter',
  name: 'Winter',
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
};

describe('LensNote', () => {
  async function mount(project: ProjectView | null, surface: string) {
    const slug = project?.slug ?? null;
    // The tenant's projects as the lens control read them, so the note names the lensed one without a request.
    const queryClient = new QueryClient();
    queryClient.setQueryData(hubProjectsKey, [WINTER]);
    await TestBed.configureTestingModule({
      imports: [LensNote],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: ProjectLens, useValue: { slug: signal(slug).asReadonly(), lensed: signal(slug !== null).asReadonly() } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(LensNote);
    fixture.componentRef.setInput('surface', surface);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  for (const surface of ['Graphs', 'Members', 'Secrets']) {
    it(`tells ${surface} that the lens does not narrow it`, async () => {
      const el = await mount(WINTER, surface);
      expect(el.querySelector('[data-testid="lens-note"]')!.textContent).toBe(
        `${surface} belong to the whole tenant: the lens on Winter does not narrow this view.`,
      );
    });
  }

  it('says nothing on All', async () => {
    const el = await mount(null, 'Graphs');
    expect(el.querySelector('[data-testid="lens-note"]')).toBeNull();
  });
});
