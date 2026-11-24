import { type EditBaseline, openLookup, readRefusal, type runnerApi, type SaveOutcome, settingValueText } from 'fleet';

import type {
  ChangeDoor,
  ChangeOp,
  RecordKind,
  SettingDraft,
  SettingLifetime,
  SettingRow,
  SettingSource,
  SettingWrite,
  SettingsChange,
  SettingsChangesPage,
  SettingsRecord,
} from './settings.domain';

const lifetimeOf = openLookup<runnerApi.Lifetime, SettingLifetime>(
  { pass: 'pass', session: 'session', boot: 'boot', immutable: 'immutable' },
  (raw) => ({ kind: 'unknown', raw }),
);

const sourceOf = openLookup<runnerApi.SettingRow['source'], SettingSource>(
  { entry: 'entry', default: 'default' },
  (raw) => ({ kind: 'unknown', raw }),
);

/** `op`, `door`, and `record_kind` are plain strings on the wire; these tables name the values the runner writes. */
const opOf = openLookup<'seed' | 'create' | 'edit' | 'retire' | 'enable' | 'replace', ChangeOp>(
  { seed: 'seed', create: 'create', edit: 'edit', retire: 'retire', enable: 'enable', replace: 'replace' },
  (raw) => ({ kind: 'unknown', raw }),
);

const doorOf = openLookup<'seed' | 'cli' | 'page' | 'api' | 'apply', ChangeDoor>(
  { seed: 'seed', cli: 'cli', page: 'page', api: 'api', apply: 'apply' },
  (raw) => ({ kind: 'unknown', raw }),
);

const recordKindOf = openLookup<'host' | 'runner' | 'workspace' | 'subscription', RecordKind>(
  { host: 'host', runner: 'runner', workspace: 'workspace', subscription: 'subscription' },
  (raw) => ({ kind: 'unknown', raw }),
);

function fromSettingRow(wire: runnerApi.SettingRow): SettingRow {
  return {
    key: wire.key,
    value: wire.value,
    source: sourceOf(wire.source),
    lifetime: lifetimeOf(wire.lifetime),
    revision: wire.revision ?? null,
    writtenAt: wire.written_at ?? null,
    writtenBy: wire.written_by ?? null,
  };
}

/** A settings read — the host's or a runner's — as the domain holds it. */
export function fromSettingsView(wire: runnerApi.SettingsView): SettingsRecord {
  const failed = wire.in_force.failed;
  return {
    revision: wire.revision,
    inForce: {
      revision: wire.in_force.revision ?? null,
      since: wire.in_force.since ?? null,
      failed: failed ? { revision: failed.revision, error: failed.error, since: failed.since } : null,
    },
    settings: wire.settings.map(fromSettingRow),
    settingsError: wire.settings_error ?? null,
  };
}

function fromChange(wire: runnerApi.ChangeView): Omit<SettingsChange, 'record'> {
  return {
    id: wire.id,
    recordedAt: wire.recorded_at,
    revision: wire.revision,
    op: opOf(wire.op),
    door: doorOf(wire.door),
    actor: wire.actor,
    diff: wire.diff.map((d) => ({
      key: d.key,
      old: d.old ? { value: d.old.value, atDefault: d.old.at_default } : null,
      new: { value: d.new.value, atDefault: d.new.at_default },
    })),
  };
}

/** A runner's change-log page as the domain holds it. */
export function fromChangesPage(wire: runnerApi.ChangesPage): SettingsChangesPage {
  return { changes: wire.changes.map((c) => ({ ...fromChange(c), record: null })), nextBefore: wire.next_before };
}

/** The host's change-log page as the domain holds it, each row naming the record it moved. */
export function fromHostChangesPage(wire: runnerApi.HostChangesPage): SettingsChangesPage {
  return {
    changes: wire.changes.map((c) => ({
      ...fromChange(c),
      record: { kind: recordKindOf(c.record_kind), key: c.record_key },
    })),
    nextBefore: wire.next_before,
  };
}

/** What an edit of `row` starts from: the record's id, the record revision it was read at, and the key's value as an
 * edit field shows it, all from one read. */
export function toSettingBaseline(recordId: string, record: SettingsRecord, row: SettingRow): EditBaseline<SettingDraft> {
  return { id: recordId, revision: record.revision, draft: { value: settingValueText(row.value) } };
}

/** A one-key write as its request body. */
export function toSettingsWrite(write: SettingWrite): runnerApi.SettingsWrite {
  return write.kind === 'set' ? { set: { [write.key]: write.value } } : { unset: [write.key] };
}

function isSettingsProblem(error: unknown): error is runnerApi.SettingsProblem {
  return typeof error === 'object' && error !== null && typeof (error as { code?: unknown }).code === 'string';
}

/** A refused write of `key` as a {@link SaveOutcome}: a revision conflict is the form line, keeping the draft; any
 * other refusal is the value's line — the problem's own `detail` for the key it names, each `errors[]` entry for
 * `key` on the value and any other key's on the form line. Anything else reads through `readRefusal`. */
export function settingRefusal(error: unknown, key: string): SaveOutcome<SettingDraft> {
  if (!isSettingsProblem(error)) {
    return readRefusal<SettingDraft>(error, { field: (path) => (path === 'set' || path === 'unset' ? 'value' : null), kinds: {} });
  }
  if (error.code === 'revision_conflict') return { ok: false, fields: {}, form: error.detail };
  const errors = error.errors ?? [];
  if (errors.length === 0) {
    return error.key === null || error.key === key
      ? { ok: false, fields: { value: error.detail }, form: null }
      : { ok: false, fields: {}, form: error.detail };
  }
  const own = errors.filter((e) => e.key === key).map((e) => e.message);
  const other = errors.filter((e) => e.key !== key).map((e) => `${e.key}: ${e.message}`);
  return {
    ok: false,
    fields: own.length > 0 ? { value: own.join('; ') } : {},
    form: other.length > 0 ? other.join('; ') : null,
  };
}
