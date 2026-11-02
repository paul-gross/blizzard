import { provideZonelessChangeDetection, type Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import { type Tone, toneColor } from '../../kit';
import { ChunkCard } from './chunk-card';
import type { ChunkCardView } from './chunk-card.model';
import { ChunkListRow } from './chunk-list-row';

/**
 * The fleet card's and list row's status colour under a real browser, with the design tokens loaded: in every tone,
 * the left edge and the id resolve to exactly the colour `toneColor` names for the view's tone, so neither app's
 * fill can colour a card through anything but its tone — and a selected one's edge yields to the selection cyan.
 * See `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const TONES: readonly Tone[] = ['running', 'needs', 'waiting', 'takeover', 'spawning', 'stale', 'done', 'idle'];

const VIEW: ChunkCardView = {
  chunkId: 'ch_01tone0000000000000000000',
  shortId: 'C-TONE',
  status: 'running',
  tone: 'running',
  statusWord: null,
  step: 'build',
  stepTitle: null,
  workLines: [{ label: 'hub#7', title: 'Migrate the event log' }],
  itemsRead: true,
  holder: { host: null, name: 'r-claude', title: 'r-claude' },
};

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

async function render(component: Type<unknown>, card: ChunkCardView, selected = false): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({ imports: [component], providers: [provideZonelessChangeDetection()] }).compileComponents();
  const fixture = TestBed.createComponent(component);
  fixture.componentRef.setInput('card', card);
  fixture.componentRef.setInput('selected', selected);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  root.style.display = 'block';
  root.style.width = '600px';
  root.style.setProperty('--list-columns', 'minmax(0, 1fr) 220px 90px');
  document.body.appendChild(root);
  await page.viewport(1280, 900);
  return root;
}

/** A colour as the browser resolves it, so a token reads the same as a computed style. */
function resolved(root: HTMLElement, color: string): string {
  const probe = document.createElement('span');
  probe.style.color = color;
  root.appendChild(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  return value;
}

const SURFACES = [
  { name: 'card', component: ChunkCard, edge: '[data-testid="chunk-card"]', id: '[data-testid="chunk-id"]' },
  { name: 'list row', component: ChunkListRow, edge: '[data-testid="list-row"]', id: '[data-testid="list-row-id"]' },
] as const;

describe('chunk card tone shell sweep (web:shell-sweep)', () => {
  beforeAll(loadDesignTokens);

  for (const surface of SURFACES) {
    it(`draws a ${surface.name}'s edge and id in toneColor of its tone, in every tone`, async () => {
      for (const tone of TONES) {
        const root = await render(surface.component, { ...VIEW, tone });
        try {
          const want = resolved(root, toneColor(tone));
          expect(getComputedStyle(root.querySelector<HTMLElement>(surface.edge)!).borderLeftColor, `${tone}: edge`).toBe(want);
          expect(getComputedStyle(root.querySelector<HTMLElement>(surface.id)!).color, `${tone}: id`).toBe(want);
        } finally {
          root.remove();
        }
      }
    });

    it(`yields a selected ${surface.name}'s edge to the selection cyan`, async () => {
      const root = await render(surface.component, { ...VIEW, tone: 'needs' }, true);
      try {
        expect(getComputedStyle(root.querySelector<HTMLElement>(surface.edge)!).borderLeftColor).toBe(resolved(root, 'var(--cyan)'));
      } finally {
        root.remove();
      }
    });
  }
});
