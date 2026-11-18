/**
 * A whole-number field's text as a number, or `null` when it is not one: blank, a sign, a fraction, exponent
 * notation, whitespace, an unsafe integer, or overflow. An invalid parse is never `0` or any default; field-specific
 * bounds apply afterwards.
 */
export function readWholeNumber(text: string): number | null {
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return Number.isSafeInteger(value) ? value : null;
}
