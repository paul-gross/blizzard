/** One choice of a select field. A disabled choice is shown but cannot be picked. */
export interface ConfigFieldOption {
  readonly value: string;
  readonly label: string;
  readonly kind?: string;
  readonly disabled?: boolean;
}

/** A mark is empty, emoji text, an existing image URL, or an uploaded image data URL. */
export type MarkImageSource = 'data' | 'url';

export function markIsImage(value: string, source: MarkImageSource = 'url'): boolean {
  return value.startsWith('data:image/') || (source === 'url' && (value.startsWith('/') || /^https?:\/\//.test(value)));
}
