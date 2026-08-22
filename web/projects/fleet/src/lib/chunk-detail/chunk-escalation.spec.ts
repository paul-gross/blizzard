import { ErrorHandler, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { ChunkDetail } from '../api/hub';
import { ChunkEscalation } from './chunk-escalation';

/** An escalation carrying both forms — the runner-composed shape. */
const WRAPPED_DETAIL: ChunkDetail = {
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
    wrapped_takeover_command:
      'blizzard runner takeover ch_01esc00000000000000000000000 --dir /var/lib/blizzard/runner',
  },
};

/** An escalation carrying only the raw field, populated with a runnable resume
 * command — the shape of a row stored before the wrapped column existed, or written
 * by a runner that could not compose the wrapped form
 * (`blizzard-context:/domain/humans.md` §Escalation). */
const RAW_ONLY_DETAIL: ChunkDetail = {
  ...WRAPPED_DETAIL,
  escalation: {
    epoch: 3,
    takeover_command: 'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
    wrapped_takeover_command: '',
  },
};

/** An escalation carrying only the raw field, populated with operator guidance prose
 * rather than a runnable command — the hub-authored, cross-graph-unresolvable shape
 * (`blizzard-context:/domain/humans.md` §Escalation). The hub never composes a
 * wrapped form. */
const PROSE_ONLY_DETAIL: ChunkDetail = {
  ...WRAPPED_DETAIL,
  escalation: {
    epoch: 3,
    takeover_command: 'cross-graph target `review` names no enabled graph — mint a graph named `review` '
      + '(or edit the choice), then requeue this chunk',
    wrapped_takeover_command: '',
  },
};

/** An escalation carrying neither form — no session was ever parked for it, so there
 * is nothing to take over. */
const NO_COMMAND_DETAIL: ChunkDetail = {
  ...WRAPPED_DETAIL,
  escalation: {
    epoch: 3,
    takeover_command: '',
    wrapped_takeover_command: '',
  },
};

/** A chunk with no open escalation at all — the common, healthy case. */
const NO_ESCALATION_DETAIL: ChunkDetail = {
  ...WRAPPED_DETAIL,
  status: 'running',
  escalation: undefined,
};

async function render(detail: ChunkDetail, extraProviders: unknown[] = []) {
  await TestBed.configureTestingModule({
    imports: [ChunkEscalation],
    providers: [provideZonelessChangeDetection(), ...extraProviders],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkEscalation);
  fixture.componentRef.setInput('detail', detail);
  await fixture.whenStable();
  return fixture;
}

describe('ChunkEscalation', () => {
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

  it('renders nothing when the chunk carries no open escalation', async () => {
    const fixture = await render(NO_ESCALATION_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).toBeNull();
  });

  it('renders the wrapped command as primary with the raw command in a collapsed fallback', async () => {
    const fixture = await render(WRAPPED_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'blizzard runner takeover ch_01esc00000000000000000000000 --dir /var/lib/blizzard/runner',
    );
    // The wrapped branch's own hint text — the socket/service-account warning
    // `docs/deployment/chunk-operations.md` calls load-bearing — was never asserted by any spec case.
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
      'no composed command for this escalation',
    );
  });

  it('renders a raw-only runnable command as the primary copyable command, with no fallback disclosure', async () => {
    const fixture = await render(RAW_ONLY_DETAIL);
    const el = fixture.nativeElement as HTMLElement;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText }, configurable: true });

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
    );
    expect(el.querySelector('[data-testid="takeover-command-raw-fallback"]')).toBeNull();
    // Framed as the unwrapped field, not as a command to run — see the prose case below
    // for why this branch cannot claim runnability.
    expect(el.querySelector('[data-testid="unwrapped-hint"]')?.textContent).toContain('either a resume');

    el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]')?.click();
    await fixture.whenStable();
    expect(writeText).toHaveBeenCalledWith('cd /work/ch_01esc00000000000000000000000 && claude --resume se_01');
  });

  it('renders hub-authored guidance prose under the same unwrapped framing, claiming no runnability', async () => {
    const fixture = await render(PROSE_ONLY_DETAIL);
    const el = fixture.nativeElement as HTMLElement;

    // The wire carries no discriminator between this and RAW_ONLY_DETAIL above, so the
    // branch is shared by construction: it stays copyable (an uncopyable command would be
    // a regression; copying prose is harmless) but tells nobody to run what it shows.
    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'mint a graph named `review`',
    );
    expect(el.querySelector('[data-testid="copy-takeover"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command-raw-fallback"]')).toBeNull();
    expect(el.querySelector('[data-testid="unwrapped-hint"]')?.textContent).not.toContain('Run the');
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

  it('does not touch the clipboard or raise an application error when the clipboard API is unavailable', async () => {
    const handleError = vi.fn();
    const fixture = await render(WRAPPED_DETAIL, [{ provide: ErrorHandler, useValue: { handleError } }]);
    const el = fixture.nativeElement as HTMLElement;
    Object.defineProperty(globalThis.navigator, 'clipboard', { value: undefined, configurable: true });
    const button = el.querySelector<HTMLButtonElement>('[data-testid="copy-takeover"]');

    button?.click();
    await fixture.whenStable();

    // A missing `if (!clipboard) return;` guard would call `.writeText` on
    // `undefined`, which Angular's own error handler swallows rather than letting
    // escape the DOM listener — `not.toThrow()` alone can't tell the two apart, so
    // this asserts the handler itself was never reached instead.
    expect(handleError).not.toHaveBeenCalled();
    expect(button?.textContent).toContain('Copy');
    expect(button?.textContent).not.toContain('Copied');
  });
});
