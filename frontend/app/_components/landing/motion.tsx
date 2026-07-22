"use client";

import {
  Children,
  cloneElement,
  isValidElement,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

/**
 * Shared motion primitives for the landing page.
 *
 * Reveals are now PURE CSS (a one-shot fade-up animation on mount with `both`
 * fill). Earlier IntersectionObserver / framer `whileInView` approaches kept
 * leaving below-the-fold sections stuck invisible inside the landing's inner
 * scroll container - CSS animation can never get stuck, so content is always
 * visible. The keyframes (`zivo-reveal-in`) live in the root layout's global
 * style. Reduced-motion is honoured there too.
 */

/** Brand spring easing - array form for framer `ease` used by a few sections. */
export const EASE = [0.32, 0.72, 0, 1] as const;

/** A single fade-up reveal. `delay` (ms) staggers it within a <Stagger>. */
export function Reveal({
  children,
  className,
  as = "div",
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "span" | "li";
  delay?: number;
}) {
  const Tag = as;
  return (
    <Tag
      className={className ? `zivo-reveal ${className}` : "zivo-reveal"}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {children}
    </Tag>
  );
}

/** Stagger container - gives each <Reveal> child an incremental delay to cascade. */
export function Stagger({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  // Stagger by child position (no render-time mutation). Stagger wraps <Reveal>
  // children, so position == reveal order; each gets an incremental 80ms delay.
  const items = Children.toArray(children).map((child, i) => {
    if (isValidElement(child) && child.type === Reveal) {
      const base = (child.props as { delay?: number }).delay ?? 0;
      return cloneElement(child as ReactElement<{ delay?: number }>, { delay: base + i * 80 });
    }
    return child;
  });
  return (
    <div className={className} style={style}>
      {items}
    </div>
  );
}
