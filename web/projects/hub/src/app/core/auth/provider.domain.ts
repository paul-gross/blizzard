/** A configured sign-in provider, as the sign-in and invitation pages offer it. `type` is the provider kind the hub
 * names (`github`, `oidc`, …). */
export interface AuthProvider {
  readonly name: string;
  readonly display_name: string;
  readonly type: string;
}
