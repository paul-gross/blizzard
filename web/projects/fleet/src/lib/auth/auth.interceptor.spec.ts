import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { client as hubClient } from '../api/hub/client.gen';
import { meApiMeGet } from '../api/hub/sdk.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { provideAuthInterceptor } from './auth.interceptor';

@Component({ selector: 'fleet-test-empty', template: '' })
class Empty {}

describe('provideAuthInterceptor (issue #93)', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub.restore();
    sessionStorage.clear();
  });

  it('routes to /login on a 401 response from any hub call', async () => {
    stub = stubRequestClient(hubClient, () => stubError(401, { detail: 'not authenticated' }));

    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([
          { path: '', component: Empty },
          { path: 'login', component: Empty },
        ]),
        provideAuthInterceptor(),
      ],
    });
    const router = TestBed.inject(Router); // forces the environment injector (and its ENVIRONMENT_INITIALIZERs) to run
    await router.navigateByUrl('/');

    await meApiMeGet({ throwOnError: false });
    // `redirectToLogin` fires `router.navigateByUrl` without awaiting it — give the
    // navigation a couple of ticks to actually land.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(router.url).toBe('/login');
  });

  it('leaves a successful response untouched', async () => {
    stub = stubRequestClient(hubClient, () => ({
      user_id: 'u',
      username: 'u',
      display_name: 'u',
      role: 'contributor',
      permissions: [],
    }));

    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([
          { path: '', component: Empty },
          { path: 'login', component: Empty },
        ]),
        provideAuthInterceptor(),
      ],
    });
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');

    const { data } = await meApiMeGet({ throwOnError: false });

    expect(data?.username).toBe('u');
    expect(router.url).toBe('/');
  });
});
