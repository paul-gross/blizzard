import { Injector, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { hubClient, meApiMeGet } from 'fleet';
import { DOOR_HEADER, provideDoorInterceptor } from './door.interceptor';

describe('provideDoorInterceptor', () => {
  it('sends X-Blizzard-Door: board on a hub request', async () => {
    const seen: string[] = [];
    const fakeFetch = async (input: Request): Promise<Response> => {
      seen.push(input.headers.get(DOOR_HEADER) ?? '');
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideDoorInterceptor()] });
    TestBed.inject(Injector); // forces the environment injector (and its ENVIRONMENT_INITIALIZERs) to run

    await meApiMeGet({ throwOnError: false });

    expect(seen).toEqual(['board']);
  });
});
