/*
 * `core/forms`' sub-barrel — the shared form-save contracts, re-exported once from the root `public-api.ts`.
 */

export type { EditBaseline } from './edit-baseline.domain';
export type { RefusalMap, SaveOutcome } from './outcome';
export { readRefusal } from './refusal.translate';
export { submitForm } from './submit';
