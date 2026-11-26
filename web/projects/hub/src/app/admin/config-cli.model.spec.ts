import { describe, expect, it } from 'vitest';

import { cliChangeCommand, detailCliCommand } from './config-cli.model';

describe('cliChangeCommand', () => {
  it('names the CLI verb that changes each kind', () => {
    expect(cliChangeCommand('work_source', 'winter')).toBe('blizzard hub source edit winter');
    expect(cliChangeCommand('repository', 'blizzard')).toBe('blizzard hub repo edit blizzard');
    expect(cliChangeCommand('credential', 'gh-main')).toBe('blizzard hub credential edit gh-main');
    expect(cliChangeCommand('secret', 'gh-token')).toBe('blizzard hub secret set gh-token');
  });
});

describe('detailCliCommand', () => {
  it('shows the command on a phone', () => {
    expect(detailCliCommand('mobile', 'secret', { name: 'gh-token' })).toBe('blizzard hub secret set gh-token');
  });

  it('shows none on a desktop', () => {
    expect(detailCliCommand('desktop', 'secret', { name: 'gh-token' })).toBeNull();
  });

  it('shows none with no record, or for a built-in one', () => {
    expect(detailCliCommand('mobile', 'work_source', null)).toBeNull();
    expect(detailCliCommand('mobile', 'work_source', { name: 'hub', built_in: true })).toBeNull();
  });
});
