// The hub settings domain: what an edit of one setting holds. No wire and no Angular.
import type { EditBaseline } from 'fleet';

/** What the edit dialog holds: the value as typed, and whether the key takes its default instead. */
export interface HubSettingDraft {
  value: string;
  useDefault: boolean;
}

/** What an edit of one setting starts from: the key as `id`, the settings revision it was read at, the draft, and the
 * key's value at that same read, which stands in for its type when the typed text is read. */
export interface HubSettingBaseline extends EditBaseline<HubSettingDraft> {
  readonly current: unknown;
}
