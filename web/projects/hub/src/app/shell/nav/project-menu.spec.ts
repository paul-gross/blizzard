import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { settle } from 'fleet/testing';

import type { LensChoice } from './project-lens.model';
import { ProjectMenu } from './project-menu';

const CHOICES: readonly LensChoice[] = [
  { slug: null, label: 'All' },
  { slug: 'blizzard', label: 'Blizzard' },
];

describe('ProjectMenu', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ProjectMenu],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function mount(current: string | null) {
    const fixture = TestBed.createComponent(ProjectMenu);
    fixture.componentRef.setInput('choices', CHOICES);
    fixture.componentRef.setInput('current', current);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const item = (slug: string) => el.querySelector<HTMLElement>(`[data-testid="mobile-titlebar-project-${slug}"]`);
    const chosen: (string | null)[] = [];
    fixture.componentInstance.choose.subscribe((slug) => chosen.push(slug));
    return { item, chosen };
  }

  it('checks the current choice, and emits another when chosen', async () => {
    const { item, chosen } = await mount(null);

    expect(item('all')?.getAttribute('aria-checked')).toBe('true');
    expect(item('blizzard')?.getAttribute('aria-checked')).toBe('false');

    item('blizzard')!.click();
    expect(chosen).toEqual(['blizzard']);
  });

  it('emits nothing when the current choice is chosen again', async () => {
    const { item, chosen } = await mount('blizzard');

    item('blizzard')!.click();

    expect(chosen).toEqual([]);
  });
});
