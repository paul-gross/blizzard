import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';
import { ProjectLinkPanel } from './project-link-panel';
import type { ProjectLinkRowVm } from './projects.model';

const row: ProjectLinkRowVm = {
  key: 'repo',
  name: 'a-very-long-repository-name-without-natural-breaks-abcdefghijk',
  detail: 'a-very-long-owner-and-repository-detail-without-natural-breaks-abcdefghijk',
  builtIn: false,
  revision: 1,
  branch: { name: 'a-very-long-branch-name-without-natural-breaks-abcdefghijk', marker: 'override', override: 'branch', inherits: 'main' },
};

describe('ProjectLinkPanel viewport layout', () => {
  afterEach(async () => { await page.viewport(1280, 800); });

  for (const width of [390, 1280]) {
    it(`keeps a long override and its badge readable at ${width}px`, async () => {
      await page.viewport(width, 800);
      await TestBed.configureTestingModule({ imports: [ProjectLinkPanel], providers: [provideZonelessChangeDetection()] }).compileComponents();
      const fixture = TestBed.createComponent(ProjectLinkPanel);
      const branchName = 'longbranch'.repeat(40);
      const shown = { ...row, branch: { ...row.branch!, name: branchName } };
      for (const [key, value] of Object.entries({ heading: 'Linked', explainer: '', rows: [shown], emptyText: 'None', linkLabel: 'Link', testid: 'p', canEdit: true })) {
        fixture.componentRef.setInput(key, value);
      }
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      fixture.detectChanges();
      await fixture.whenStable();

      const link = root.querySelector<HTMLElement>('.row')!;
      const name = link.querySelector<HTMLElement>('.branch-name')!;
      const badge = link.querySelector<HTMLElement>('.branch fleet-kit-badge')!;
      const label = badge.firstElementChild as HTMLElement;
      const bounds = link.getBoundingClientRect();
      const badgeBounds = badge.getBoundingClientRect();
      expect(name.textContent).toContain(branchName);
      expect(name.getBoundingClientRect().height).toBeGreaterThan(parseFloat(getComputedStyle(name).lineHeight) || 20);
      expect(name.scrollWidth).toBeLessThanOrEqual(name.clientWidth + 1);
      expect(badge.textContent?.trim()).toBe('override');
      expect(getComputedStyle(badge).whiteSpace).toBe('nowrap');
      expect(badgeBounds.width).toBeGreaterThan(50);
      expect(label.scrollWidth).toBeLessThanOrEqual(label.clientWidth + 1);
      expect(badgeBounds.left).toBeGreaterThanOrEqual(bounds.left - 1);
      expect(badgeBounds.right).toBeLessThanOrEqual(bounds.right + 1);
      expect(root.scrollWidth).toBeLessThanOrEqual(width);
      fixture.destroy();
      root.remove();
    });

    it(`keeps link content readable at ${width}px`, async () => {
      await page.viewport(width, 800);
      await TestBed.configureTestingModule({ imports: [ProjectLinkPanel], providers: [provideZonelessChangeDetection()] }).compileComponents();
      const fixture = TestBed.createComponent(ProjectLinkPanel);
      const shown = width === 390 ? row : { ...row, name: 'repository', detail: 'owner/repository', branch: { ...row.branch!, name: 'feature/branch' } };
      for (const [key, value] of Object.entries({ heading: 'Linked', explainer: '', rows: [shown], emptyText: 'None', linkLabel: 'Link', testid: 'p', canEdit: width > 600 })) {
        fixture.componentRef.setInput(key, value);
      }
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      fixture.detectChanges();
      await fixture.whenStable();

      const link = root.querySelector<HTMLElement>('.row')!;
      const box = link.getBoundingClientRect();
      const elements = ['.name', '.detail', '.branch-name'].map((selector) => link.querySelector<HTMLElement>(selector)!);
      for (const [index, element] of elements.entries()) {
        const rect = element.getBoundingClientRect();
        expect(element.textContent).toContain([shown.name, shown.detail, shown.branch!.name][index]);
        expect(rect.width).toBeGreaterThan(30);
        expect(rect.height).toBeGreaterThan(0);
        expect(rect.left).toBeGreaterThanOrEqual(box.left - 1);
        expect(rect.right).toBeLessThanOrEqual(box.right + 1);
        expect(element.scrollWidth).toBeLessThanOrEqual(element.clientWidth + 1);
      }
      expect(root.scrollWidth).toBeLessThanOrEqual(width);
      if (width === 390) {
        expect(elements[1].getBoundingClientRect().top).toBeGreaterThanOrEqual(elements[0].getBoundingClientRect().bottom);
      } else {
        const actions = link.querySelector<HTMLElement>('.right')!;
        expect(Math.abs(elements[0].getBoundingClientRect().top - elements[1].getBoundingClientRect().top)).toBeLessThan(5);
        expect(Math.abs(elements[0].getBoundingClientRect().top - elements[2].getBoundingClientRect().top)).toBeLessThan(5);
        expect(actions.getBoundingClientRect().top).toBeLessThan(elements[0].getBoundingClientRect().bottom);
        expect(actions.getBoundingClientRect().bottom).toBeLessThanOrEqual(box.bottom);
        expect(link.querySelector('[data-testid^="p-edit-branch-"]')).not.toBeNull();
        expect(link.querySelector('[data-testid^="p-unlink-"]')).not.toBeNull();
      }
      fixture.destroy();
      root.remove();
    });
  }
});
