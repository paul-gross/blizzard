/** A setting's value as an edit field shows it: a string as itself, any other value as JSON. */
export function settingValueText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** What an edit's text reads as: the value to send, or why it cannot be sent. */
export type ParsedSettingValue = { readonly value: unknown } | { readonly error: string };

/** The value to send for an edit's `text` of a setting now holding `current`: a string setting takes the text as
 * typed, and any other setting reads it as JSON. */
export function parseSettingText(current: unknown, text: string): ParsedSettingValue {
  if (typeof current === 'string') return { value: text };
  try {
    return { value: JSON.parse(text) as unknown };
  } catch {
    return { error: 'Enter a valid JSON value.' };
  }
}
