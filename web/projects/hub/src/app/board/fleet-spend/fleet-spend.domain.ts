/** The fleet's cost over a window, as the shell's header renders it: the exact cost, the estimate beside it, and
 * whether the exact figure is partial. */
export interface FleetSpend {
  readonly cost_usd: number;
  readonly estimated_cost_usd: number | null;
  readonly cost_partial: boolean;
}
