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
    wrapped_takeover_command:
      'blizzard runner takeover ch_01esc00000000000000000000000 --dir /var/lib/blizzard/runner',
  },
};

/** An escalation carrying neither form — the hub-authored, bounce-cap shape
 * (blizzard#251 round-2 F9): no parked session was ever composed, so there is
 * nothing to run at all. */
const NO_COMMAND_DETAIL: ChunkDetail = {
  ...RAW_ONLY_DETAIL,
  escalation: {
    epoch: 3,
    takeover_command: '',
    wrapped_takeover_command: '',
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
  let originalClipboardDescriptor: PropertyDescriptor | undefined;

  beforeEach(() => {
    originalClipboardDescriptor = Object.getOwnPropertyDescriptor(globalThis.navigator, 'clipboard');
  });

  afterEach(() => {
    if (originalClipboardDescriptor) {
      Object.defineProperty(globalThis.navigator, 'clipboard', originalClipboardDescriptor);
    } else {
      delete (globalThis.navigator as { clipboard?: Clipboard }).clipboard;
    }
  });

  it('renders the wrapped command as primary with the raw command in a collapsed fallback', async () => {
    const fixture = await render(WRAPPED_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'blizzard runner takeover ch_01esc00000000000000000000000 --dir /var/lib/blizzard/runner',
    );
    // The wrapped branch's own hint text — the socket/service-account warning
    // `docs/deployment.md` calls load-bearing (round-1 F7) — was never asserted by
    // any spec case (round-2 F24).
    expect(el.querySelector('.esc-hint')?.textContent).toContain("Run as the runner's service account");
    const fallback = el.querySelector<HTMLDetailsElement>('[data-testid="takeover-command-raw-fallback"]');
    expect(fallback).not.toBeNull();
    expect(fallback?.textContent).toContain('cd /work/ch_01esc00000000000000000000000 && claude --resume se_01');
    expect(fallback?.open).toBe(false);
  });

  it('renders no command box or copy button, with a distinct message, when the escalation carries neither form', async () => {
    const fixture = await render(NO_COMMAND_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')).toBeNull();
    expect(el.querySelector('[data-testid="copy-takeover"]')).toBeNull();
    expect(el.querySelector('[data-testid="takeover-command-raw-fallback"]')).toBeNull();
    expect(el.querySelector('[data-testid="no-command-hint"]')?.textContent).toContain(
      'no command to run for this escalation',
    );
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
    expect(el.querySelector('.esc-hint')?.textContent).toContain('Use the following to continue');
  });

  it('copies the wrapped command when it is primary, flipping the label and resetting it after the timeout', async () => {
    const fixture = await render(WRAPPED_DETAIL);
    const el = fixture.nativeElement as HTMLElement;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText }, configurable: true });
    const button = el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]');

    expect(button?.textContent).toContain('Copy');

    vi.useFakeTimers();
    try {
      button?.click();
      // Flushes the clipboard promise's microtask (which sets `copied`) alongside
      // any pending timers, then a synchronous CD pass renders it.
      await vi.advanceTimersByTimeAsync(0);
      fixture.detectChanges();

      expect(writeText).toHaveBeenCalledWith(
        'blizzard runner takeover ch_01esc00000000000000000000000 --dir /var/lib/blizzard/runner',
      );
      expect(button?.textContent).toContain('Copied');

      // The component's own timeout (see copyTakeover) is 1500ms.
      await vi.advanceTimersByTimeAsync(1600);
      fixture.detectChanges();
      expect(button?.textContent).toContain('Copy');
      expect(button?.textContent).not.toContain('Copied');
    } finally {
      vi.useRealTimers();
    }
  });

  it('copies the raw command when it is primary (no wrapped command present)', async () => {
    const fixture = await render(RAW_ONLY_DETAIL);
    const el = fixture.nativeElement as HTMLElement;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText }, configurable: true });
    const button = el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]');

    button?.click();
    await fixture.whenStable();

    expect(writeText).toHaveBeenCalledWith('cd /work/ch_01esc00000000000000000000000 && claude --resume se_01');
    expect(button?.textContent).toContain('Copied');
  });

  it('does nothing and does not throw when the clipboard API is unavailable', async () => {
    const fixture = await render(RAW_ONLY_DETAIL);
    const el = fixture.nativeElement as HTMLElement;
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: undefined, configurable: true });
    const button = el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]');

    expect(() => button?.click()).not.toThrow();
    await fixture.whenStable();

    expect(button?.textContent).toContain('Copy');
    expect(button?.textContent).not.toContain('Copied');
  });
});
