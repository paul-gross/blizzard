import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { settle } from 'fleet/testing';

import { ProjectLensControl } from './project-lens-control';
import type { LensChoice } from './project-lens.model';

const CHOICES: readonly LensChoice[] = [
  { slug: null, label: 'All' },
  { slug: 'blizzard', label: 'Blizzard' },
  { slug: 'zeta', label: 'Zeta' },
];

describe('ProjectLensControl', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ProjectLensControl],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function mount(current: string | null) {
    const fixture = TestBed.createComponent(ProjectLensControl);
    fixture.componentRef.setInput('choices', CHOICES);
    fixture.componentRef.setInput('current', current);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const chip = (slug: string) =>
      el.querySelector<HTMLElement>(`[data-testid="project-lens-${slug}"]`);
    const chosen: (string | null)[] = [];
    fixture.componentInstance.choose.subscribe((slug) => chosen.push(slug));
    return { fixture, chip, chosen };
  }

  it('renders every choice and marks the current one', async () => {
    const { chip } = await mount('blizzard');

    expect(chip('all')?.getAttribute('aria-pressed')).toBe('false');
    expect(chip('blizzard')?.getAttribute('aria-pressed')).toBe('true');
    expect(chip('zeta')?.textContent).toContain('Zeta');
  });

  it('emits the chosen slug, and null for All', async () => {
    const { chip, chosen } = await mount('blizzard');

    chip('zeta')!.click();
    chip('all')!.click();

    expect(chosen).toEqual(['zeta', null]);
  });

  it('emits nothing when the current choice is chosen again', async () => {
    const { chip, chosen } = await mount('blizzard');

    chip('blizzard')!.click();

    expect(chosen).toEqual([]);
  });
});
