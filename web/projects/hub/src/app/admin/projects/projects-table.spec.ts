import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ProjectsTable } from './projects-table';
import type { ProjectRowVm } from './projects.model';

const ROWS: readonly ProjectRowVm[] = [
  { key: 'proj_b', name: 'Blizzard', projectId: 'proj_01ABCDEFGHJKMNPQRSTVWXYZ12', needs: null, wired: true },
  { key: 'proj_c', name: 'Celestial', projectId: 'proj_01ABCDEFGHJKMNPQRSTVWXYZ34', needs: 'needs a runner', wired: false },
  { key: 'proj_d', name: 'Dormant', projectId: 'proj_01ABCDEFGHJKMNPQRSTVWXYZ56', needs: null, wired: false },
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

  it('shows a light and the name, the needs badge when wanting, and the short id', async () => {
    const { el } = await mount(ROWS);
    const light = (key: string) => el.querySelector(`[data-testid="projects-table-light-${key}"]`)!;
    expect(el.querySelector('[data-testid="projects-table-row-proj_b"]')!.textContent).toContain('Blizzard');
    expect(light('proj_b').getAttribute('aria-label')).toBe('wired');
    expect(light('proj_b').classList).toContain('wired');
    expect(light('proj_c').getAttribute('aria-label')).toBe('missing wiring');
    expect(light('proj_c').classList).toContain('missing');
    expect(light('proj_d').getAttribute('aria-label')).toBe('not yet judged');
    expect(light('proj_d').classList).not.toContain('wired');
    expect(light('proj_d').classList).not.toContain('missing');
    expect(el.querySelector('[data-testid="projects-table-needs-proj_b"]')).toBeNull();
    expect(el.querySelector('[data-testid="projects-table-needs-proj_c"]')!.textContent).toContain('needs a runner');
    expect(el.querySelector('[data-testid="projects-table-ref-proj_b"]')!.textContent).toBe('PROJ-YZ12');
    const row = el.querySelector('[data-testid="projects-table-row-proj_b"]')!.textContent!;
    expect(row).not.toMatch(/\br\d/);
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
