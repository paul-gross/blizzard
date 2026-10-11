import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ProjectsTable } from './projects-table';
import type { ProjectRowVm } from './projects.model';

const ROWS: readonly ProjectRowVm[] = [
  {
    key: 'proj_b',
    name: 'Blizzard',
    revision: 4,
    figures: ['3 linked sources', '3 linked repos', '5 in queue'],
    needs: null,
    wired: true,
  },
  { key: 'proj_c', name: 'Celestial', revision: 1, figures: [], needs: 'needs a runner', wired: false },
];

describe('ProjectsTable', () => {
  async function mount(rows: readonly ProjectRowVm[], selectedKey: string | null = null) {
    await TestBed.configureTestingModule({
      imports: [ProjectsTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProjectsTable);
    fixture.componentRef.setInput('rows', rows);
    fixture.componentRef.setInput('state', rows.length === 0 ? 'empty' : 'ready');
    fixture.componentRef.setInput('selectedKey', selectedKey);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('shows each project with its revision and figures, a dot when wired and what a wanting one needs', async () => {
    const { el } = await mount(ROWS);
    const blizzard = el.querySelector('[data-testid="projects-table-row-proj_b"]')!.textContent!;
    expect(blizzard).toContain('Blizzard');
    expect(blizzard).toContain('r4');
    expect(el.querySelector('[data-testid="projects-table-figures-proj_b"]')!.textContent).toContain(
      '3 linked sources',
    );
    expect(el.querySelector('[data-testid="projects-table-wired-proj_b"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="projects-table-needs-proj_b"]')).toBeNull();
    expect(el.querySelector('[data-testid="projects-table-needs-proj_c"]')!.textContent).toContain('needs a runner');
    expect(el.querySelector('[data-testid="projects-table-wired-proj_c"]')).toBeNull();
    expect(el.querySelector('[data-testid="projects-table-figures-proj_c"]')).toBeNull();
  });

  it('picks a project by its id', async () => {
    const { fixture, el } = await mount(ROWS);
    const picked: string[] = [];
    fixture.componentInstance.pick.subscribe((key) => picked.push(key));
    el.querySelector<HTMLElement>('[data-testid="projects-table-row-proj_c"]')!.click();
    expect(picked).toEqual(['proj_c']);
  });

  it('says so when there is no project', async () => {
    const { el } = await mount([]);
    expect(el.querySelector('[data-testid="projects-table-empty"]')).not.toBeNull();
  });
});
