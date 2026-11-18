import { describe, expect, it } from 'vitest';
import {
  fromChunkCountsView,
  fromRepositoryLinkView,
  fromSourceLinkView,
} from './project-read.translate';

describe('project read translation', () => {
  it('retains unlinked and built-in source state', () => {
    const link = {
      project_id: 'p',
      name: 'hub',
      slug: null,
      linked: false,
      built_in: true,
      revision: null,
    };
    expect(fromSourceLinkView(link)).toEqual(link);
  });

  it('retains repository order and branch inheritance fields', () => {
    const link = {
      project_id: 'p',
      repository_id: 'r',
      name: 'repo',
      slug: 'repo',
      linked: false,
      revision: 3,
      position: null,
      base_branch: null,
      effective_base_branch: 'main',
    };
    expect(fromRepositoryLinkView(link)).toEqual(link);
  });

  it('carries all counts including the nullable day count', () => {
    const counts = {
      delivering: 1,
      done: 2,
      done_since: null,
      needs_human: 3,
      not_ready: 4,
      paused: 5,
      ready: 6,
      running: 7,
      stopped: 8,
      terminal: 9,
      total: 10,
      waiting_on_human: 11,
    };
    expect(fromChunkCountsView(counts)).toEqual(counts);
  });
});
