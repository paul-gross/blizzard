import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type AskRow, LocalAsksView } from './local-asks-view';

async function render(rows: readonly AskRow[]) {
  await TestBed.configureTestingModule({
    imports: [LocalAsksView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalAsksView);
  fixture.componentRef.setInput('rows', rows);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('LocalAsksView', () => {
  it('renders an open ask with its chunk ref and question text — no query stub required', async () => {
    const { el } = await render([{ questionId: 'q-1', chunkRef: 'C-3YJ9', askedFor: '30s', question: 'Proceed with the migration?' }]);

    const row = el.querySelector('[data-testid="ask-row"]');
    expect(row?.getAttribute('data-question-id')).toBe('q-1');
    expect(row?.querySelector('.q')?.textContent).toBe('Proceed with the migration?');
    expect(row?.querySelector('.chunk')?.textContent).toBe('C-3YJ9');
    expect(row?.querySelector('.asked')?.textContent).toContain('30s');
  });

  it('renders one row per given ask', async () => {
    const { el } = await render([
      { questionId: 'q-1', chunkRef: 'C-1', askedFor: '30s', question: 'A?' },
      { questionId: 'q-2', chunkRef: 'C-2', askedFor: '1m', question: 'B?' },
    ]);

    expect(el.querySelectorAll('[data-testid="ask-row"]')).toHaveLength(2);
  });
});
