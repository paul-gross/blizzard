import { secretView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { secret } from './secret.builders';
import {
  fromSecretView,
  SECRET_REFUSALS,
  secretReferenceRefs,
  toCreateRequest,
  toReplaceBaseline,
} from './secret.translate';

const PEM = '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo\n-----END RSA PRIVATE KEY-----\n';

describe('fromSecretView', () => {
  it('reads a served secret into the domain, with no value', () => {
    const read = fromSecretView(
      secretView({ name: 'tok', revision: 2, replaced_by: 'pgross', references: [{ kind: 'repository', key: 'r', label: null }] }),
    );
    expect(read).toMatchObject({ name: 'tok', revision: 2, retired: false, replacedBy: 'pgross' });
    expect(read.references).toEqual([{ kind: 'repository', key: 'r', label: null }]);
    expect(Object.keys(read)).not.toContain('value');
  });
});

describe('secretReferenceRefs', () => {
  it('hands the referring records back as record refs', () => {
    expect(secretReferenceRefs(secret())[0]).toEqual({ kind: 'work_source', key: 'blizzard', label: null });
  });
});

describe('toReplaceBaseline', () => {
  it('captures the name, the revision, and a blank draft', () => {
    expect(toReplaceBaseline(secret({ name: 'tok', revision: 4 }))).toEqual({ id: 'tok', revision: 4, draft: { value: '' } });
  });
});

describe('toCreateRequest', () => {
  it('trims the name and sends the value as typed, line breaks intact', () => {
    expect(toCreateRequest({ name: ' app-key ', value: PEM })).toEqual({ name: 'app-key', value: PEM });
  });
});

describe('SECRET_REFUSALS', () => {
  it('maps the body fields and a taken name, and leaves the rest to the form line', () => {
    expect(SECRET_REFUSALS.field('name')).toBe('name');
    expect(SECRET_REFUSALS.field('value')).toBe('value');
    expect(SECRET_REFUSALS.field('other')).toBeNull();
    expect(SECRET_REFUSALS.kinds['urn:blizzard:problem:secret-name-taken']).toBe('name');
  });
});
