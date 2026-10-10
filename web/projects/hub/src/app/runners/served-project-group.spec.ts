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
});
