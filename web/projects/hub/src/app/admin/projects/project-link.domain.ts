/** A selection is valid only while it names one of the offered eligible records. */
export interface ProjectLinkDraft { name: string }
export interface ProjectLinkChoice { readonly value: string; readonly label: string }
export type ProjectLinkKind = 'source' | 'repository';

export function projectLinkDraft(choices: readonly ProjectLinkChoice[]): ProjectLinkDraft {
  return { name: choices[0]?.value ?? '' };
}

export function projectLinkChoices(kind: ProjectLinkKind | null, sources: readonly ProjectLinkChoice[], repositories: readonly ProjectLinkChoice[]): readonly ProjectLinkChoice[] {
  return kind === 'source' ? sources : repositories;
}

export function projectLinkError(draft: ProjectLinkDraft, choices: readonly ProjectLinkChoice[]): string | null {
  return draft.name !== '' && choices.some((choice) => choice.value === draft.name) ? null : 'Choose an available record.';
}
