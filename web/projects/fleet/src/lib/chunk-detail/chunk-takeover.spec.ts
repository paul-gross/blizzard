import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { ChunkDetail } from '../api/hub';
import { ChunkTakeover } from './chunk-takeover';

/** An escalation carrying only the raw `takeover_command` — the shape a runner too old
 * to compose the wrapped form (or a hub-composed escalation) sends. */
const RAW_ONLY_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  status: 'needs_human',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  work_refs: [],
  history: [],
  artifacts: [],
  escalation: {
    epoch: 3,
    takeover_command: 'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
    wrapped_takeover_command: '',
  },
};

/** An escalation carrying both forms — the runner-composed shape (blizzard#251). */
const WRAPPED_DETAIL: ChunkDetail = {
  ...RAW_ONLY_DETAIL,
  escalation: {
    epoch: 3,
    takeover_command: 'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
    wrapped_takeover_command: 'blizzard runner takeover ch_01esc00000000000000000000000',
  },
};

async function render(detail: ChunkDetail) {
  await TestBed.configureTestingModule({
    imports: [ChunkTakeover],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkTakeover);
  fixture.componentRef.setInput('detail', detail);
  await fixture.whenStable();
  return fixture;
}

describe('ChunkTakeover', () => {
  it('renders the wrapped command as primary with the raw command in a collapsed fallback', async () => {
    const fixture = await render(WRAPPED_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'blizzard runner takeover ch_01esc',
    );
    const fallback = el.querySelector<HTMLDetailsElement>('[data-testid="takeover-command-raw-fallback"]');
    expect(fallback).not.toBeNull();
    expect(fallback?.textContent).toContain('cd /work/ch_01esc00000000000000000000000 && claude --resume se_01');
    expect(fallback?.open).toBe(false);
  });

  it('renders the raw command as primary with no fallback disclosure when unwrapped', async () => {
    const fixture = await render(RAW_ONLY_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
    );
    expect(el.querySelector('[data-testid="takeover-command-raw-fallback"]')).toBeNull();
    expect(el.querySelector('[data-testid="copy-takeover"]')).not.toBeNull();
    expect(el.querySelector('.esc-hint')?.textContent).toContain('Run the takeover command to enter its session');
  });

  it('copies the wrapped command when it is primary', async () => {
    const fixture = await render(WRAPPED_DETAIL);
    const el = fixture.nativeElement as HTMLElement;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText }, configurable: true });

    el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]')?.click();

    expect(writeText).toHaveBeenCalledWith('blizzard runner takeover ch_01esc00000000000000000000000');
  });

  it('copies the raw command when it is primary (no wrapped command present)', async () => {
    const fixture = await render(RAW_ONLY_DETAIL);
    const el = fixture.nativeElement as HTMLElement;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText }, configurable: true });

    el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]')?.click();

    expect(writeText).toHaveBeenCalledWith('cd /work/ch_01esc00000000000000000000000 && claude --resume se_01');
  });
});
