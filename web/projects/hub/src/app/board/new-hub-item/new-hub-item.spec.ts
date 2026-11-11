import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { ProjectLens } from '../../core/project-lens';
import { NewHubItem } from './new-hub-item';
import { CREATED_ITEM } from './new-hub-item.test-data';

const PROJECT = { color: 'blue', created_at: '2026-01-01T00:00:00Z', description: '', name: 'Alpha', project_id: 'pr_alpha', revision: 1, slug: 'alpha' };
const URGENCY = { urgency_id: 'u_standard', name: 'Standard', slug: 'standard', default: true, retired: false, color: 'gray', emoji: null, icon: null, created_at: '2026-01-01T00:00:00Z', created_by: 'operator', open_chunks: 0, place: 1, revision: 1 };

describe('NewHubItem', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function mount(options: { lens?: string; denied?: boolean; fail?: boolean } = {}) {
    stub = stubHubClient({
      'GET /api/me': () => options.denied ? { ...OPERATOR_ME_RESPONSE, permissions: [] } : OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => ({ projects: [PROJECT] }),
      'GET /api/urgencies': () => ({ urgencies: [URGENCY, { ...URGENCY, urgency_id: 'u_retired', name: 'Retired', default: false, retired: true }] }),
      'POST /api/work-sources/{source}/items': () => options.fail ? stubError(422, { detail: 'Creation refused' }) : CREATED_ITEM,
    });
    await TestBed.configureTestingModule({
      imports: [NewHubItem],
      providers: [provideZonelessChangeDetection(), provideRouter([]), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })), { provide: ProjectLens, useValue: { slug: signal(options.lens ?? null) } }],
    }).compileComponents();
    const fixture = TestBed.createComponent(NewHubItem);
    await settle(fixture);
    return fixture;
  }

  it('withholds the action without chunk:control', async () => {
    const fixture = await mount({ denied: true });
    expect(fixture.nativeElement.querySelector('[data-testid="new-hub-item"]')).toBeNull();
  });

  it('requires a project on All and starts at the tenant default urgency', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="new-hub-item-urgency"]')?.getAttribute('aria-label')).toBe('Urgency: Standard');
    expect(el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-submit"]')!.disabled).toBe(true);
    const title = el.querySelector<HTMLInputElement>('[data-testid="new-hub-item-title"]')!;
    title.value = 'Title'; title.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-submit"]')!.disabled).toBe(true);
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-project"]')!.click();
    await settle(fixture);
    const option = document.querySelector<HTMLElement>('[role="option"]')!;
    expect(option.textContent).toContain('Alpha');
    option.click();
    await settle(fixture);
    const submit = el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-submit"]')!;
    expect(submit.disabled).toBe(true);
    const body = el.querySelector<HTMLTextAreaElement>('[data-testid="new-hub-item-body"]')!;
    body.value = '   '; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(submit.disabled).toBe(true);
    body.value = 'Body'; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(submit.disabled).toBe(false);
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-urgency"]')!.click();
    await settle(fixture);
    expect(document.querySelector('[role="listbox"]')?.textContent).not.toContain('Retired');
  });

  it('uses the lens, creates and emits the minted chunk', async () => {
    const fixture = await mount({ lens: 'alpha' });
    const created: string[] = [];
    fixture.componentInstance.created.subscribe((id) => created.push(id));
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="new-hub-item-project"]')).toBeNull();
    const title = el.querySelector<HTMLInputElement>('[data-testid="new-hub-item-title"]')!;
    const body = el.querySelector<HTMLTextAreaElement>('[data-testid="new-hub-item-body"]')!;
    title.value = ' Title '; title.dispatchEvent(new Event('input'));
    body.value = 'Body'; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-submit"]')!.click();
    await settle(fixture);
    expect(created).toEqual(['ch_created']);
    expect(el.querySelector('[data-testid="new-hub-item-dialog"]')).toBeNull();
    const request = stub.forRoute('/api/work-sources/hub/items', 'POST')[0];
    expect(request.body).toEqual({ title: 'Title', body: 'Body', urgency: 'u_standard' });
    expect(new URLSearchParams(request.search).get('project')).toBe('alpha');
  });

  it('keeps the entered values open when creation fails', async () => {
    const fixture = await mount({ lens: 'alpha', fail: true });
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    await settle(fixture);
    const title = el.querySelector<HTMLInputElement>('[data-testid="new-hub-item-title"]')!;
    title.value = 'Title'; title.dispatchEvent(new Event('input'));
    const body = el.querySelector<HTMLTextAreaElement>('[data-testid="new-hub-item-body"]')!;
    body.value = 'Body'; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-submit"]')!.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="new-hub-item-dialog"]')).not.toBeNull();
    expect(title.value).toBe('Title');
    expect(el.textContent).toContain('Creation refused');
  });
});
