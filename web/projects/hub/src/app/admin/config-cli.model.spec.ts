import { RecordKind } from 'fleet';
import { describe, expect, it } from 'vitest';

import { cliChangeCommand, detailCliCommand } from './config-cli.model';

describe('cliChangeCommand', () => {
  it('names the CLI verb that changes each kind', () => {
    expect(cliChangeCommand(RecordKind.WORK_SOURCE, 'winter')).toBe('blizzard hub source edit winter');
    expect(cliChangeCommand(RecordKind.REPOSITORY, 'blizzard')).toBe('blizzard hub repo edit blizzard');
    expect(cliChangeCommand(RecordKind.SECRET, 'gh-token')).toBe('blizzard hub secret set gh-token');
  });
});

describe('detailCliCommand', () => {
  it('shows the command on a phone', () => {
    expect(detailCliCommand('mobile', RecordKind.SECRET, { name: 'gh-token' })).toBe('blizzard hub secret set gh-token');
  });

  it('shows none on a desktop', () => {
    expect(detailCliCommand('desktop', RecordKind.SECRET, { name: 'gh-token' })).toBeNull();
  });

  it('shows none with no record, or for a built-in one', () => {
    expect(detailCliCommand('mobile', RecordKind.WORK_SOURCE, null)).toBeNull();
    expect(detailCliCommand('mobile', RecordKind.WORK_SOURCE, { name: 'hub', built_in: true })).toBeNull();
  });
});
