import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * A selected board card draws one selection edge — the outer `.card`'s — and none inside
 * it: the kit select row under `bare` carries no padding, border or background of its own,
 * the card's PR links sit inside the card surface, and the content's inset from the card
 * edge is the card's own padding alone, at 800px, 390px and 320px
 * (`bzh:narrow-viewport-tier-rule`). Under a real browser — see
 * `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const CARD: BoardCard = {
  chunkId: 'ch_01selectedcard0000000000000',
  shortId: 'C-3YJ9',
  status: 'running',
  terminal: false,
  promotable: false,
  node: 'build',
  nodeId: 'nd_build',
  pointerLabels: ['owner/repo#1'],
  costUsd: 1,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
  openPrs: [{ repo: 'owner/repo', number: 7, url: 'https://example.test/pr/7' }],
} as BoardCard;

const WIDTHS = [800, 390, 320];

/** The design tokens are a global stylesheet the app build loads, never a standalone mount. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

function tokenColor(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

describe('board card selected state shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`draws only the outer card's selection edge, with the PR links inside it, at width ${width}`, async () => {
      await loadDesignTokens();
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [BoardCardComponent],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(BoardCardComponent);
      fixture.componentRef.setInput('card', CARD);
      fixture.componentRef.setInput('selected', true);
      await fixture.whenStable();
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await page.viewport(width, 600);

      try {
        const card = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
        const button = root.querySelector<HTMLElement>('fleet-kit-select-row button')!;
        const cardStyle = getComputedStyle(card);
        const buttonStyle = getComputedStyle(button);

        expect(cardStyle.borderLeftColor).toBe(tokenColor('--cyan'));
        expect(buttonStyle.backgroundColor).toBe('rgba(0, 0, 0, 0)');
        for (const side of ['Top', 'Right', 'Bottom', 'Left'] as const) {
          expect(buttonStyle[`border${side}Width`], `inner button ${side} border`).toBe('0px');
        }

        const cardRect = card.getBoundingClientRect();
        const links = root.querySelector<HTMLElement>('[data-testid="card-delivery"]')!.getBoundingClientRect();
        expect(links.left).toBeGreaterThanOrEqual(cardRect.left);
        expect(links.right).toBeLessThanOrEqual(cardRect.right);
        expect(links.bottom).toBeLessThanOrEqual(cardRect.bottom);

        // The content's inset from the card edge is the card's border + padding alone.
        const content = root.querySelector<HTMLElement>('[data-testid="work-ref-chip"]')!.getBoundingClientRect();
        const inset = content.left - cardRect.left;
        const expected = parseFloat(cardStyle.borderLeftWidth) + parseFloat(cardStyle.paddingLeft);
        expect(inset, `content inset ${inset} vs card border+padding ${expected}`).toBeCloseTo(expected, 0);

        // The open control spans the card's padding box, and the id ending the step row stays inside it.
        const buttonRect = button.getBoundingClientRect();
        expect(buttonRect.width).toBeCloseTo(card.clientWidth, 0);
        expect(buttonRect.height).toBeCloseTo(card.clientHeight, 0);
        const id = root.querySelector<HTMLElement>('[data-testid="chunk-id"]')!.getBoundingClientRect();
        expect(id.right).toBeLessThanOrEqual(cardRect.right);
      } finally {
        root.remove();
      }
    });
  }
});
