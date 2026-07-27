/*
 * A container-query resolver for specs.
 *
 * The unit/component tier runs on jsdom, which parses `@container` rules into
 * the CSSOM but never *evaluates* them — `getComputedStyle` reports the base
 * declaration at every width, so a spec asserting a tiered collapse would pass
 * against a component that had no breakpoints at all. This helper closes that
 * hole by resolving the rules the component actually ships: it reads the parsed
 * `@container` blocks out of the CSSOM, keeps the ones whose condition holds at
 * a given container width, and applies their matching declarations over the
 * base computed style.
 *
 * Deliberately narrow: `max-width` conditions only, which is the whole
 * vocabulary the board header's tiers use. A `min-width` or range condition
 * would need matching support here before a spec could trust it — see
 * {@link resolveContainerStyle}'s throw.
 */

/** A `@container <name> (max-width: <px>)` block recovered from the CSSOM. */
interface ContainerTier {
  readonly maxWidth: number;
  readonly rules: readonly CSSStyleRule[];
}

const CONTAINER_PRELUDE = /^@container\s+([\w-]+)\s*\(\s*max-width:\s*(\d+(?:\.\d+)?)px\s*\)/;

/** Whether a CSSOM rule is a container at-rule — duck-typed rather than
 * `instanceof CSSContainerRule`, which jsdom does not expose as a global. */
function isContainerRule(rule: CSSRule): rule is CSSGroupingRule {
  return rule.cssText.startsWith('@container');
}

/** Every `@container <name> (max-width: …)` tier declared anywhere in the
 * document's stylesheets, in source order. */
function containerTiers(containerName: string): readonly ContainerTier[] {
  const tiers: ContainerTier[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    let rules: CSSRule[];
    try {
      rules = Array.from(sheet.cssRules);
    } catch {
      // A cross-origin sheet refuses `cssRules`; none of ours are, so skip it
      // rather than let one poison the whole sweep.
      continue;
    }
    for (const rule of rules) {
      if (!isContainerRule(rule)) continue;
      const prelude = CONTAINER_PRELUDE.exec(rule.cssText);
      if (!prelude) {
        throw new Error(`container-query helper supports only \`(max-width: …px)\` conditions, got: ${rule.cssText.split('{')[0].trim()}`);
      }
      if (prelude[1] !== containerName) continue;
      tiers.push({
        maxWidth: Number(prelude[2]),
        rules: Array.from(rule.cssRules).filter((inner): inner is CSSStyleRule => 'selectorText' in inner),
      });
    }
  }
  return tiers;
}

/**
 * The value `property` would resolve to on `element` if its `containerName`
 * query container were `width` pixels wide — the base computed style, with
 * every matching `@container` tier applied over it in source order.
 *
 * Selector matching runs against the real element, so Angular's view-
 * encapsulation attributes are honored exactly as the browser would honor them.
 */
export function resolveContainerStyle(
  element: Element,
  property: string,
  options: { readonly containerName: string; readonly width: number },
): string {
  let value = getComputedStyle(element).getPropertyValue(property);
  for (const tier of containerTiers(options.containerName)) {
    if (options.width > tier.maxWidth) continue;
    for (const rule of tier.rules) {
      if (!element.matches(rule.selectorText)) continue;
      const declared = rule.style.getPropertyValue(property);
      if (declared) value = declared;
    }
  }
  return value;
}

/** Whether `element` is hidden (`display: none`) at the given container width. */
export function hiddenAtContainerWidth(
  element: Element,
  options: { readonly containerName: string; readonly width: number },
): boolean {
  return resolveContainerStyle(element, 'display', options) === 'none';
}
