import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { ProjectView } from 'fleet';

import { ProjectRecordPanel, type ProjectTab, type ProjectUnlinkEvent } from './project-record-panel';
import { projectTabs } from './project-overview.model';
import type { ProjectLinkRowVm } from './projects.model';

const PROJECT: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: 'The platform',
  color: 'teal',
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
};
const JIRA: ProjectLinkRowVm = { key: 'wsrc_j', name: 'jira', detail: 'jira · acme', builtIn: false, revision: 2 };

describe('ProjectRecordPanel', () => {
  async function mount(tab: ProjectTab = 'overview', canEdit = true) {
    await TestBed.configureTestingModule({
      imports: [ProjectRecordPanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProjectRecordPanel);
    const set = (name: string, value: unknown) => fixture.componentRef.setInput(name, value);
    set('project', PROJECT);
    set('canEdit', canEdit);
    set('tabs', projectTabs(1, 0));
    set('tab', tab);
    set('sourceRows', [JIRA]);
    set('repositoryRows', []);
    set('servedLines', []);
    set('repositoriesState', 'empty');
    set('servedState', 'empty');
    set('revisions', []);
    set('revisionsState', 'empty');
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement, set };
  }

  const byTestid = (el: HTMLElement, testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);

  it('renders the record header and the overview cards from plain inputs', async () => {
    const { el } = await mount();
    expect(byTestid(el, 'admin-projects-detail')?.textContent).toContain('Blizzard');
    expect(byTestid(el, 'admin-projects-description')?.textContent).toContain('The platform');
    expect(byTestid(el, 'admin-projects-draws')?.textContent).toContain('jira');
    expect(byTestid(el, 'admin-projects-lands')?.textContent).toContain('No repository is linked');
    expect(byTestid(el, 'admin-projects-served-by')?.textContent).toContain('No runner serves this project.');
    expect(byTestid(el, 'admin-projects-color')?.textContent).toContain('teal');
  });

  it('re-emits edit, color, tab, link and unlink', async () => {
    const { fixture, el, set } = await mount('sources');
    const seen: unknown[] = [];
    const panel = fixture.componentInstance;
    panel.edit.subscribe(() => seen.push('edit'));
    panel.color.subscribe(() => seen.push('color'));
    panel.tabChange.subscribe((tab) => seen.push(`tab:${tab}`));
    panel.link.subscribe((kind) => seen.push(`link:${kind}`));
    panel.unlink.subscribe((event: ProjectUnlinkEvent) => seen.push(`unlink:${event.kind}:${event.row.key}`));

    byTestid(el, 'admin-projects-edit')!.click();
    byTestid(el, 'admin-projects-color-open')!.click();
    byTestid(el, 'admin-projects-tab-repositories')!.click();
    byTestid(el, 'admin-projects-sources-link')!.click();
    byTestid(el, 'admin-projects-sources-unlink-jira')!.click();
    set('tab', 'overview');
    await fixture.whenStable();
    byTestid(el, 'admin-projects-draws-open')!.click();

    expect(seen).toEqual(['edit', 'color', 'tab:repositories', 'link:source', 'unlink:source:wsrc_j', 'tab:sources']);
  });

  it('disables the unlink of every row in the pending set', async () => {
    const { fixture, el, set } = await mount('sources');
    expect((byTestid(el, 'admin-projects-sources-unlink-jira') as HTMLButtonElement).disabled).toBe(false);
    set('pendingSourceKeys', new Set(['wsrc_j']));
    await fixture.whenStable();
    expect((byTestid(el, 'admin-projects-sources-unlink-jira') as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers no write controls to a viewer who may not edit', async () => {
    const { el } = await mount('overview', false);
    expect(byTestid(el, 'admin-projects-edit')).toBeNull();
    expect(byTestid(el, 'admin-projects-color-open')).toBeNull();
  });
});
