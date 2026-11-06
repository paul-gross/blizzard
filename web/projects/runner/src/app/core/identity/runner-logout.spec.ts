import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { stubError, stubRunnerClient, type RequestClientStub } from 'fleet/testing';
import { RunnerLogout } from './runner-logout';

describe('RunnerLogout', () => {
  let stub: RequestClientStub;
  let fail = false;

  beforeEach(() => {
    fail = false;
    stub = stubRunnerClient({
      'POST /api/auth/logout': () => (fail ? stubError(500, { detail: 'session store down' }) : undefined),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
  });

  afterEach(() => stub.restore());

  it('reloads on success', async () => {
    const owner = TestBed.inject(RunnerLogout);
    const reload = vi.spyOn(owner as unknown as { reload: () => void }, 'reload').mockImplementation(() => undefined);

    await owner.logout();

    expect(reload).toHaveBeenCalledTimes(1);
    expect(owner.error()).toBeNull();
  });

  it('records the failure, does not reload, and does not reject', async () => {
    fail = true;
    const owner = TestBed.inject(RunnerLogout);
    TestBed.tick();
    const reload = vi.spyOn(owner as unknown as { reload: () => void }, 'reload').mockImplementation(() => undefined);

    await expect(owner.logout()).resolves.toBeUndefined();

    expect(reload).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(owner.error()).toBe('session store down'));
    expect(owner.pending()).toBe(false);
  });
});
