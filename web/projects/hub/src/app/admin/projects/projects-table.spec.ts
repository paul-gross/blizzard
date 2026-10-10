import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ProjectsTable } from './projects-table';
import type { ProjectRowVm } from './projects.model';

const ROWS: readonly ProjectRowVm[] = [
  { key: 'proj_b', name: 'Blizzard', slug: 'blizzard', description: 'The platform', revision: 4, unserved: false },
  { key: 'proj_c', name: 'Celestial', slug: 'celestial', description: '', revision: 1, unserved: true },
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

  it('shows each project with its slug and revision, and badges the one no runner serves', async () => {
    const { el } = await mount(ROWS);
    const blizzard = el.querySelector('[data-testid="projects-table-row-proj_b"]')!.textContent!;
    expect(blizzard).toContain('Blizzard');
    expect(blizzard).toContain('blizzard');
    expect(blizzard).toContain('r4');
    expect(el.querySelector('[data-testid="projects-table-unserved-proj_b"]')).toBeNull();
    expect(el.querySelector('[data-testid="projects-table-unserved-proj_c"]')!.textContent).toContain('no runner');
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
