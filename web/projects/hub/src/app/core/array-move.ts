/**
 * `items` with the item at `from` moved to `to`, as a new array. Both indices clamp into the array's bounds, as a
 * drag-and-drop list reports them, so an out-of-range index lands at the nearer end.
 */
export function movedItem<T>(items: readonly T[], from: number, to: number): T[] {
  const moved = [...items];
  if (moved.length === 0) return moved;
  const last = moved.length - 1;
  const source = Math.max(0, Math.min(from, last));
  const target = Math.max(0, Math.min(to, last));
  const [item] = moved.splice(source, 1);
  moved.splice(target, 0, item);
  return moved;
}
