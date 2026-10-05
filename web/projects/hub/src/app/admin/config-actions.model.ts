import type { ViewportMode } from 'fleet';

/** The write controls a record's detail renders. */
export interface ConfigActionsVm {
  /** Whether a field edit is offered. */
  readonly edit: boolean;
  /** Whether a value replace is offered — a secret's alone. */
  readonly replace: boolean;
  /** The lifecycle control: Retire for a live record, Enable for a retired one. */
  readonly lifecycle: 'retire' | 'enable';
  /** Why Retire is held off, or `null` when it is offered. */
  readonly retireBlocked: string | null;
}

/** The shown record, as far as its write controls care. */
export interface ConfigActionRecord {
  readonly built_in?: boolean;
  readonly retired?: boolean;
}

/** The note a secret's Retire carries while something live refers to it. */
export const SECRET_IN_USE_NOTE = 'In use — retire or repoint what refers to it first.';

/**
 * The write controls for a record — `null` for none. A phone is read-only, so no
 * control is built there; a desktop needs `config:edit`; a built-in record has none.
 * `referenceCount` is the active referrers a secret has, which hold its Retire back.
 */
export function configActions(
  mode: ViewportMode,
  canEdit: boolean,
  record: ConfigActionRecord | null | undefined,
  options: {
    readonly replace?: boolean;
    readonly referenceCount?: number;
  } = {},
): ConfigActionsVm | null {
  if (mode === 'mobile' || !canEdit || !record || record.built_in) return null;
  const retired = record.retired ?? false;
  return {
    edit: !(options.replace ?? false),
    replace: options.replace ?? false,
    lifecycle: retired ? 'enable' : 'retire',
    retireBlocked: !retired && (options.referenceCount ?? 0) > 0 ? SECRET_IN_USE_NOTE : null,
  };
}

/** A write's refusal, tied to the record it was for. */
export interface ConfigFailure {
  readonly key: string;
  readonly message: string;
}

/** The refusal to show for the record `key` selects — one raised for another record is
 * not shown, so a failed retire does not follow the operator to the next row. */
export function failureFor(failure: ConfigFailure | null, key: string | null): string | null {
  return failure !== null && failure.key === key ? failure.message : null;
}
