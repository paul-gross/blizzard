import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { OPERATOR_ME_RESPONSE, stubHubClient } from 'fleet/testing';
import { page } from 'vitest/browser';
import { ProjectLens } from '../../core/project-lens';
import { NewHubItem } from './new-hub-item';

const STANDARD = { urgency_id: 'u_standard', name: 'Standard', slug: 'standard', default: true, retired: false, color: 'gray', emoji: null, icon: null, created_at: '2026-01-01T00:00:00Z', created_by: 'operator', open_chunks: 0, place: 1, revision: 1 };

describe('new hub item dialog shell sweep', () => {
  it('keeps the mounted dialog and controls within the viewport on desktop and phone', async () => {
    const stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => ({ projects: [] }),
      'GET /api/urgencies': () => ({ urgencies: [STANDARD] }),
    });
    try {
      await TestBed.configureTestingModule({
        imports: [NewHubItem],
        providers: [provideZonelessChangeDetection(), provideRouter([]), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })), { provide: ProjectLens, useValue: { slug: signal('alpha') } }],
      }).compileComponents();
      const fixture = TestBed.createComponent(NewHubItem);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      try {
        for (let i = 0; i < 100; i += 1) {
          fixture.detectChanges();
          if (root.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]:not(:disabled)')) break;
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        root.querySelector<HTMLButtonElement>('[data-testid="new-hub-item"]')!.click();
        fixture.detectChanges();
        for (const width of [1280, 320]) {
          await page.viewport(width, 800);
          fixture.detectChanges();
          const dialog = root.querySelector<HTMLElement>('[data-testid="new-hub-item-dialog"]')!;
          const rect = dialog.getBoundingClientRect();
          expect(rect.left).toBeGreaterThanOrEqual(0);
          expect(rect.right).toBeLessThanOrEqual(width);
          for (const id of ['new-hub-item-title', 'new-hub-item-body', 'new-hub-item-urgency', 'new-hub-item-submit']) {
            const control = root.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
            const box = control.getBoundingClientRect();
            expect(box.left, id).toBeGreaterThanOrEqual(rect.left);
            expect(box.right, id).toBeLessThanOrEqual(rect.right);
          }
        }
      } finally {
        root.remove();
      }
    } finally {
      stub.restore();
    }
  });
});
