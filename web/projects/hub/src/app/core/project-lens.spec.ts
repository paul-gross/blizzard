import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { ProjectLens, lensLink, lensSegmentOf, lensedUrl, unlensedUrl, viewOf } from './project-lens';

@Component({ selector: 'app-test-lens-view', template: '' })
class TestLensView {}

describe('the lens in a board URL', () => {
  it('reads the project segment of a lensed URL, and none of a URL on All', () => {
    expect(lensSegmentOf('/p/blizzard/board?chunk=chk_1')).toBe('blizzard');
    expect(lensSegmentOf('/p/proj_01J/events')).toBe('proj_01J');
    expect(lensSegmentOf('/board')).toBeNull();
    expect(lensSegmentOf('/')).toBeNull();
  });

  it('removes and adds the lens prefix, keeping the view, the query and the fragment', () => {
    expect(unlensedUrl('/p/blizzard/gardening/runs?since=7d')).toBe('/gardening/runs?since=7d');
    expect(unlensedUrl('/p/blizzard')).toBe('/');
    expect(unlensedUrl('/events')).toBe('/events');
    expect(lensedUrl('/gardening/runs?since=7d', 'blizzard')).toBe('/p/blizzard/gardening/runs?since=7d');
    expect(lensedUrl('/', 'blizzard')).toBe('/p/blizzard');
    expect(lensedUrl('/board', null)).toBe('/board');
  });

  it('never lenses a public page', () => {
    expect(lensedUrl('/login', 'blizzard')).toBe('/login');
    expect(lensedUrl('/invite/tok_1', 'blizzard')).toBe('/invite/tok_1');
  });

  it('keeps a link to a view under the lens, and leaves relative, public and already-lensed links alone', () => {
    expect(lensLink('/board', 'blizzard')).toBe('/p/blizzard/board');
    expect(lensLink(['/board', 'chunk', 'chk_1'], 'blizzard')).toEqual(['/p', 'blizzard', 'board', 'chunk', 'chk_1']);
    expect(lensLink(['/board/chunk', 'chk_1'], 'blizzard')).toEqual(['/p', 'blizzard', 'board', 'chunk', 'chk_1']);
    expect(lensLink(['chk_1'], 'blizzard')).toEqual(['chk_1']);
    expect(lensLink(['/login'], 'blizzard')).toEqual(['/login']);
    expect(lensLink(['/p', 'other', 'board'], 'blizzard')).toEqual(['/p', 'other', 'board']);
    expect(lensLink(['/board'], null)).toEqual(['/board']);
  });

  it('keeps the view and its tab across a lens change, and drops the record', () => {
    expect(viewOf('/p/blizzard/board/chunk/chk_1?tab=general')).toBe('/board');
    expect(viewOf('/gardening/scopes/web-ui?q=x')).toBe('/gardening/scopes');
    expect(viewOf('/admin/work-sources/github')).toBe('/admin/work-sources');
    expect(viewOf('/p/blizzard')).toBe('/board');
  });
});

describe('ProjectLens', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestLensView }] },
          { path: '**', component: TestLensView },
        ]),
      ],
    });
  });

  it('follows the URL: a link keeps the lens the board is on', async () => {
    const lens = TestBed.inject(ProjectLens);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/events');
    expect(lens.slug()).toBe('blizzard');
    expect(lens.link(['/board', 'chunk', 'chk_1'])).toEqual(['/p', 'blizzard', 'board', 'chunk', 'chk_1']);

    await router.navigateByUrl('/events');
    expect(lens.slug()).toBeNull();
    expect(lens.link(['/board'])).toEqual(['/board']);
  });

  it('switches the lens and keeps the view the board is on', async () => {
    const lens = TestBed.inject(ProjectLens);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/findings/fnd_1?routine=r');
    await lens.switchTo('blizzard');
    expect(router.url).toBe('/p/blizzard/gardening/findings');

    await lens.switchTo('celestial');
    expect(router.url).toBe('/p/celestial/gardening/findings');

    await lens.switchTo(null);
    expect(router.url).toBe('/gardening/findings');
  });

  it('holds a notice until it is dismissed or the lens is switched', async () => {
    const lens = TestBed.inject(ProjectLens);
    await TestBed.inject(Router).navigateByUrl('/board');

    lens.notify('No project “gone” in this tenant. Showing all projects.');
    expect(lens.notice()).toContain('gone');
    lens.dismissNotice();
    expect(lens.notice()).toBeNull();

    lens.notify('again');
    await lens.switchTo('blizzard');
    expect(lens.notice()).toBeNull();
  });

  it('releases a resolution waiting on the shell once the shell has entered its tenant', async () => {
    const lens = TestBed.inject(ProjectLens);
    let released = false;
    void lens.whenReady().then(() => (released = true));

    lens.settle(false);
    await Promise.resolve();
    expect(released).toBe(false);

    lens.settle(true);
    await Promise.resolve();
    expect(released).toBe(true);
    await expect(lens.whenReady()).resolves.toBeUndefined();
  });
});
