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

  async function mount(options: { lens?: string; denied?: boolean; fail?: boolean; held?: Promise<void> } = {}) {
    stub = stubHubClient({
      'GET /api/me': () => options.denied ? { ...OPERATOR_ME_RESPONSE, permissions: [] } : OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => ({ projects: [PROJECT] }),
      'GET /api/urgencies': () => ({ urgencies: [URGENCY, { ...URGENCY, urgency_id: 'u_retired', name: 'Retired', default: false, retired: true }] }),
      'POST /api/work-sources/{source}/items': async () => { await options.held; return options.fail ? stubError(422, { detail: 'Creation refused' }) : CREATED_ITEM; },
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

  it('starts at the tenant default urgency and withholds retired ones', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="new-hub-item-urgency"]')?.getAttribute('aria-label')).toBe('Urgency: Standard');
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-urgency"]')!.click();
    await settle(fixture);
    expect(document.querySelector('[role="listbox"]')?.textContent).not.toContain('Retired');
  });

  it('shows a field error and sends no request for each missing field', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    await settle(fixture);
    const save = () => el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-save"]')!;
    const fieldErrors = () => el.querySelectorAll('fleet-kit-field fleet-kit-error-line').length;
    save().click();
    await settle(fixture);
    // title, body and project are missing; the urgency defaulted.
    expect(fieldErrors()).toBe(3);
    expect(stub.forRoute('/api/work-sources/hub/items', 'POST')).toHaveLength(0);
    const title = el.querySelector<HTMLInputElement>('[data-testid="new-hub-item-title"]')!;
    title.value = 'Title'; title.dispatchEvent(new Event('input'));
    const body = el.querySelector<HTMLTextAreaElement>('[data-testid="new-hub-item-body"]')!;
    body.value = '   '; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(fieldErrors()).toBe(2);
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-project"]')!.click();
    await settle(fixture);
    document.querySelector<HTMLElement>('[role="option"]')!.click();
    await settle(fixture);
    expect(fieldErrors()).toBe(1);
    save().click();
    await settle(fixture);
    expect(stub.forRoute('/api/work-sources/hub/items', 'POST')).toHaveLength(0);
    body.value = 'Body'; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(fieldErrors()).toBe(0);
    save().click();
    await settle(fixture);
    const request = stub.forRoute('/api/work-sources/hub/items', 'POST')[0];
    expect(new URLSearchParams(request.search).get('project')).toBe('pr_alpha');
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
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-save"]')!.click();
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
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-save"]')!.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="new-hub-item-dialog"]')).not.toBeNull();
    expect(title.value).toBe('Title');
    expect(el.textContent).toContain('Creation refused');
  });
  it('refuses Escape and backdrop dismissal while creation is pending and preserves a failed draft', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    const fixture = await mount({ lens: 'alpha', fail: true, held });
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    await settle(fixture);
    const title = el.querySelector<HTMLInputElement>('[data-testid="new-hub-item-title"]')!;
    const body = el.querySelector<HTMLTextAreaElement>('[data-testid="new-hub-item-body"]')!;
    title.value = 'Draft'; title.dispatchEvent(new Event('input'));
    body.value = 'Body'; body.dispatchEvent(new Event('input'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item-save"]')!.click();
    fixture.detectChanges();
    const dialog = el.querySelector<HTMLElement>('[data-testid="new-hub-item-dialog"]')!;
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    dialog.parentElement!.click();
    el.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
    fixture.detectChanges();
    expect(el.querySelector('[data-testid="new-hub-item-dialog"]')).not.toBeNull();
    expect(title.value).toBe('Draft');
    release();
    await settle(fixture);
    expect(el.textContent).toContain('Creation refused');
    expect(title.value).toBe('Draft');
  });

});
