import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';

import { type AskRow, LocalAsksView } from './app-asks-view';

async function render(rows: readonly AskRow[]) {
  await TestBed.configureTestingModule({
    imports: [LocalAsksView],
    providers: [provideZonelessChangeDetection(), provideRouter([{ path: 'board/chunk/:chunkId', component: LocalAsksView }])],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalAsksView);
  fixture.componentRef.setInput('rows', rows);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement, fixture, router: TestBed.inject(Router) };
}

describe('LocalAsksView', () => {
  it('renders an open ask with its chunk ref, runner, and question text — no query stub required', async () => {
    const { el } = await render([{ questionId: 'q-1', runner: 'r-claude', chunkRef: 'C-3YJ9', chunkId: 'ch_3YJ9', askedFor: '30s', question: 'Proceed with the migration?' }]);

    const row = el.querySelector('[data-testid="ask-row"]');
    expect(row?.getAttribute('data-question-id')).toBe('q-1');
    expect(row?.querySelector('.q')?.textContent).toBe('Proceed with the migration?');
    expect(row?.querySelector('.chunk')?.textContent).toBe('C-3YJ9');
    expect(row?.querySelector('.asked')?.textContent).toBe('[30s]');
    expect(row?.querySelector('[data-testid="ask-runner"]')?.textContent).toBe('r-claude');
  });

  it('renders one row per given ask, each naming the runner it is open on', async () => {
    const { el } = await render([
      { questionId: 'q-1', runner: 'r1', chunkRef: 'C-1', chunkId: 'ch_1', askedFor: '30s', question: 'A?' },
      { questionId: 'q-2', runner: 'r2', chunkRef: 'C-2', chunkId: 'ch_2', askedFor: '1m', question: 'B?' },
    ]);

    expect(el.querySelectorAll('[data-testid="ask-row"]')).toHaveLength(2);
    expect(Array.from(el.querySelectorAll('[data-testid="ask-runner"]')).map((r) => r.textContent)).toEqual(['r1', 'r2']);
  });

  it('opens the selected ask’s full chunk id through a native local link, without hub-write CLI instructions', async () => {
    const { el, fixture, router } = await render([
      { questionId: 'qn_01ABC', runner: 'r1', chunkRef: 'C-1', chunkId: 'ch_01ABC', askedFor: '30s', question: 'A?' },
      { questionId: 'qn_01DEF', runner: 'r1', chunkRef: 'C-2', chunkId: 'ch_01DEF', askedFor: '1m', question: 'B?' },
    ]);

    const links = Array.from(el.querySelectorAll<HTMLAnchorElement>('a.chunk'));
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/board/chunk/ch_01ABC', '/board/chunk/ch_01DEF']);
    expect(links[1].getAttribute('aria-label')).toBe('Open question on chunk C-2');
    links[1].click();
    await fixture.whenStable();
    expect(router.url).toBe('/board/chunk/ch_01DEF');
    expect(el.querySelector('.route')).toBeNull();
    expect(el.textContent).not.toContain('blizzard hub');
  });
});
