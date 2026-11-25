import { hostSubscriptionView } from 'fleet/testing';

import { runnerSubscription } from '../core/host-subscription.translate';
import { hostWorkspace } from '../core/host-workspace.builders';
import type { HostRecord } from './host-records.domain';
import { recordRefusal, subscriptionRecord, toRecordBaseline, toRecordCreate, toRecordEdit, workspaceRecord } from './host-records.translate';

const WEB: HostRecord = {
  kind: 'workspace',
  key: 'web',
  revision: 3,
  state: 'active',
  settings: {
    root: '/srv/web',
    envs: ['web-1', 'web-2'],
    max_environments: 2,
    repo: [{ name: 'blizzard', url: 'https://x/b.git' }],
  },
};

const PROBLEM = {
  type: 'urn:blizzard:problem:settings-invalid',
  title: 'settings invalid',
  status: 422,
  detail: 'the workspace is invalid',
  code: 'settings_invalid',
  key: null,
  current: null,
  errors: null,
  record_kind: 'workspace',
  record_key: 'web',
};

describe('host records translate', () => {
  it('reads a workspace and a subscription through the core translators as records', () => {
    expect(workspaceRecord(hostWorkspace({ workspace_id: 'web', revision: 2, state: 'retired', settings: { root: '/r' } }))).toEqual({
      kind: 'workspace',
      key: 'web',
      revision: 2,
      state: 'retired',
      settings: { root: '/r' },
    });
    const sub = runnerSubscription(hostSubscriptionView({ slug: 'max', name: 'Max', provider: 'anthropic', revision: 5, settings: { name: 'Max' } }));
    expect(subscriptionRecord(sub)).toEqual({ kind: 'subscription', key: 'max', revision: 5, state: 'active', settings: { name: 'Max' } });
  });

  it('captures an edit`s baseline from one read, each editable key as its field shows it', () => {
    expect(toRecordBaseline(WEB)).toEqual({
      id: 'web',
      revision: 3,
      draft: {
        root: '/srv/web',
        provider: '',
        envs: 'web-1, web-2',
        max_environments: '2',
        repo: 'blizzard https://x/b.git',
        base_branch: '',
        prompt: '',
        prompt_file: '',
        prompt_package: '',
      },
    });
    expect(toRecordBaseline({ ...WEB, revision: null })).toBeNull();
  });

  it('states every non-blank field of a create', () => {
    expect(toRecordCreate('workspace', { id: 'web', root: '/srv', envs: '', max_environments: '2' })).toEqual({
      write: { kind: 'workspace', body: { id: 'web', root: '/srv', max_environments: 2 } },
    });
    expect(toRecordCreate('subscription', { slug: 'max', name: '', provider: 'anthropic' })).toEqual({ errors: { name: 'Required.' } });
  });

  it('states only the fields an edit changed, a cleared one as null', () => {
    const before = toRecordBaseline(WEB)!.draft;
    expect(toRecordEdit('workspace', before, { ...before, root: '/srv/new', envs: '', id: 'ignored' })).toEqual({
      write: { kind: 'workspace', body: { root: '/srv/new', envs: null } },
    });
    expect(toRecordEdit('workspace', before, { ...before, max_environments: 'x' })).toEqual({
      errors: { max_environments: 'Enter a whole number.' },
    });
  });

  it('puts a revision conflict on the form line, and each refused key beside its field', () => {
    expect(recordRefusal({ ...PROBLEM, status: 409, code: 'revision_conflict', detail: 'moved' }, 'workspace')).toEqual({
      ok: false,
      fields: {},
      form: 'moved',
    });
    expect(
      recordRefusal(
        {
          ...PROBLEM,
          errors: [
            { key: 'root', message: 'no such directory' },
            { key: 'runner', message: 'unknown' },
          ],
        },
        'workspace',
      ),
    ).toEqual({ ok: false, fields: { root: 'no such directory' }, form: 'runner: unknown' });
    expect(recordRefusal({ ...PROBLEM, key: 'max_environments', detail: 'too many' }, 'workspace')).toEqual({
      ok: false,
      fields: { max_environments: 'too many' },
      form: null,
    });
    expect(recordRefusal({ ...PROBLEM, code: 'record_exists', detail: 'workspace web exists' }, 'workspace')).toEqual({
      ok: false,
      fields: {},
      form: 'workspace web exists',
    });
  });
});
