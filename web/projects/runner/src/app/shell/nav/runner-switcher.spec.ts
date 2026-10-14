import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { provideActiveRunner } from '../../core/active-runner';
import { RunnerSwitcher } from './runner-switcher';

const RUNNERS = {
  items: [
    { runner_id: 'rn_1', name: 'r-claude', state: 'serving', pause: { local: false, hub: false, effective: false } },
    { runner_id: 'rn_2', name: 'r-chatgpt', state: 'serving', pause: { local: true, hub: false, effective: true } },
    { runner_id: null, name: 'r-broken', state: 'halted', halted_reasons: ['no hub token'] },
  ],
};

describe('RunnerSwitcher', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render() {
    stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === '/api/host/runners' ? RUNNERS : {}));
    await TestBed.configureTestingModule({
      imports: [RunnerSwitcher],
      providers: [
        provideZonelessChangeDetection(),
        provideActiveRunner('rn_1'),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnerSwitcher);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('lists every runner, the active one selected and a braked one naming its brake', async () => {
    const { el } = await render();

    const chip = (testid: string) => el.querySelector(`[data-testid="${testid}"]`);
    expect(chip('runner-switcher-rn_1')?.getAttribute('aria-pressed')).toBe('true');
    expect(chip('runner-switcher-rn_2')?.getAttribute('aria-pressed')).toBe('false');
    expect(chip('runner-switcher-rn_2')?.textContent).toContain('paused');
    expect(chip('runner-switcher-r-broken')?.hasAttribute('disabled')).toBe(true);
  });

  it('opens a chosen runner’s board', async () => {
    const { el } = await render();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    el.querySelector<HTMLElement>('[data-testid="runner-switcher-rn_2"]')?.click();

    expect(navigate).toHaveBeenCalledWith(['/runners', 'rn_2', 'board']);
  });

  it('stays put when the active runner is chosen again', async () => {
    const { el } = await render();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    el.querySelector<HTMLElement>('[data-testid="runner-switcher-rn_1"]')?.click();

    expect(navigate).not.toHaveBeenCalled();
  });
});
