import { DestroyRef, EnvironmentInjector, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient } from '@tanstack/angular-query-experimental';
import { Subject } from 'rxjs';
import { vi } from 'vitest';

import { LiveInvalidationSpine } from './live-invalidation-spine';
import { type SseService } from './sse.service';

describe('LiveInvalidationSpine boundary', () => {
  it('subscribes to named events and translates once before both consumers', () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const events = new Subject<{ type: string; data: unknown }>();
    const connect = vi.fn(() => ({ events, reopens: signal(0), status: signal('open'), authFailed: signal(false), close: vi.fn() }));
    const data = { value: 'translated' };
    const mapFrame = vi.fn(() => ({ type: 'domain', data }));
    const onFrame = vi.fn();
    const keys = vi.fn(() => []);
    const registry = vi.fn(() => keys);
    TestBed.runInInjectionContext(() => {
      const spine = new LiveInvalidationSpine({
        sse: { connect } as unknown as SseService,
        queryClient: new QueryClient(),
        injector: TestBed.inject(EnvironmentInjector),
        destroyRef: TestBed.inject(DestroyRef),
        streamUrl: '/stream', eventTypes: ['wire'], mapFrame, onFrame, registry, coalesceWindowMs: 1000,
      });
      spine.start();
    });
    events.next({ type: 'wire', data: { original: true } });
    expect(connect).toHaveBeenCalledWith('/stream', { events: ['wire'] });
    expect(mapFrame).toHaveBeenCalledTimes(1);
    expect(onFrame).toHaveBeenCalledWith('domain', data);
    expect(registry).toHaveBeenCalledWith('domain');
    expect(keys).toHaveBeenCalledWith(data);
  });
});
