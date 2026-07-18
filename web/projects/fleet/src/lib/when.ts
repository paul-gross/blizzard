/** Compact recency stamp for a board timestamp — the closer the instant, the shorter
 * the text: today reads as bare `HH:MM`, yesterday as `Yesterday HH:MM`, anything
 * older as the date alone (`2026/07/16` — a day-old judgement's minute no longer
 * matters). Local time, 24-hour clock, empty string for an unparseable input.
 *
 * `now` is injectable for tests only; callers pass the timestamp alone. */
export function formatWhen(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number): string => `${n}`.padStart(2, '0');
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const daysAgo = Math.round(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) /
      86_400_000,
  );
  if (daysAgo <= 0) return hm;
  if (daysAgo === 1) return `Yesterday ${hm}`;
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}
