import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ServedProjectGroup } from './served-project-group';

async function render(served: readonly string[], unassigned: boolean, unresolved: readonly string[]): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(ServedProjectGroup);
  fixture.componentRef.setInput('served', served);
  fixture.componentRef.setInput('unassigned', unassigned);
  fixture.componentRef.setInput('unresolved', unresolved);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

const texts = (el: HTMLElement, testid: string) =>
  [...el.querySelectorAll(`[data-testid="${testid}"]`)].map((badge) => badge.textContent?.trim());

describe('ServedProjectGroup', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ServedProjectGroup],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('names every project the runner serves', async () => {
    const el = await render(['Default', 'Glacier'], false, []);

    expect(el.querySelector('.served-label')?.textContent?.trim()).toBe('Serves:');
    expect(texts(el, 'runner-served-project')).toEqual(['Default', 'Glacier']);
    expect(el.querySelector('[data-testid="runner-unassigned"]')).toBeNull();
    expect(el.querySelector('[data-testid="runner-unresolved"]')).toBeNull();
  });

  it('drops its Serves: label where a surrounding grid labels it', async () => {
    const fixture = TestBed.createComponent(ServedProjectGroup);
    fixture.componentRef.setInput('served', ['Default']);
    fixture.componentRef.setInput('unassigned', false);
    fixture.componentRef.setInput('unresolved', []);
    fixture.componentRef.setInput('labelled', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.served-label')).toBeNull();
    expect(texts(el, 'runner-served-project')).toEqual(['Default']);
  });

  it('marks a runner that declares no project as unassigned', async () => {
    const el = await render([], true, []);

    expect(texts(el, 'runner-unassigned')).toEqual(['UNASSIGNED']);
    expect(el.querySelector('[data-testid="runner-served-project"]')).toBeNull();
  });

  it('names each unresolved entry as the runner wrote it, beside the projects it does serve', async () => {
    const el = await render(['Default'], false, ['celestial', 'proj_gone']);

    expect(texts(el, 'runner-served-project')).toEqual(['Default']);
    expect(texts(el, 'runner-unresolved')).toEqual(['UNRESOLVED celestial', 'UNRESOLVED proj_gone']);
    expect(el.querySelector('[data-entry="celestial"]')?.getAttribute('title')).toContain('celestial');
  });

  it("renders a hosted runner's bindings — project, workspace, agents in use of declared — and marks an unreported workspace", async () => {
    const fixture = TestBed.createComponent(ServedProjectGroup);
    fixture.componentRef.setInput('served', ['Default']);
    fixture.componentRef.setInput('unassigned', false);
    fixture.componentRef.setInput('unresolved', []);
    fixture.componentRef.setInput('bindings', [
      { project: 'Default', workspaceId: 'ws-1', agents: 3, agentsInUse: 1, unreported: false },
      { project: 'Glacier', workspaceId: 'ws-9', agents: 1, agentsInUse: 0, unreported: true },
    ]);
    fixture.componentRef.setInput('subscription', 'Anthropic Max');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runner-served"]')).toBeNull();
    expect(texts(el, 'runner-binding-project')).toEqual(['Default', 'Glacier']);
    expect(texts(el, 'runner-binding-workspace')).toEqual(['ws-1', 'ws-9']);
    expect(texts(el, 'runner-binding-agents')).toEqual(['1 / 3 agents', '0 / 1 agents']);
    const unreported = el.querySelectorAll('[data-testid="runner-binding-unreported"]');
    expect(unreported).toHaveLength(1);
    expect(unreported[0].closest('[data-workspace-id]')?.getAttribute('data-workspace-id')).toBe('ws-9');
    expect(texts(el, 'runner-subscription-name')).toEqual(['Anthropic Max']);
    expect(el.querySelector('[data-testid="runner-subscription-unrostered"]')).toBeNull();
  });

  it("flags a hosted runner's subscription its host's roster does not carry", async () => {
    const fixture = TestBed.createComponent(ServedProjectGroup);
    fixture.componentRef.setInput('served', []);
    fixture.componentRef.setInput('unassigned', false);
    fixture.componentRef.setInput('unresolved', []);
    fixture.componentRef.setInput('bindings', []);
    fixture.componentRef.setInput('subscription', 'zzz');
    fixture.componentRef.setInput('subscriptionUnrostered', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(texts(el, 'runner-subscription-name')).toEqual(['zzz']);
    expect(texts(el, 'runner-subscription-unrostered')).toEqual(['UNROSTERED']);
  });
});
