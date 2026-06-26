"use client";

import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";
import { useReducedMotion } from "framer-motion";

/**
 * Shared motion primitives for the landing page.
 *
 * Scroll reveals use a NATIVE IntersectionObserver rather than framer's
 * `whileInView` — the latter does not fire inside the landing's inner scroll
 * container under React 19, which left every below-the-fold section invisible.
 * Native IO observes geometric intersection with the viewport regardless of which
 * element scrolls, so it's reliable here. Everything is gated on reduced-motion.
 */

/** Brand spring easing — array form for framer `ease`, string form for CSS. */
export const EASE = [0.32, 0.72, 0, 1] as const;
const EASE_CSS = "cubic-bezier(0.32, 0.72, 0, 1)";
export const DURATION_MS = 620;

function useInViewOnce() {
  const ref = useRef<HTMLElement | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            io.disconnect();
            return;
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return { ref, shown };
}

/** Single scroll-triggered reveal. Lifts in once when it enters the viewport. */
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
  const reduce = useReducedMotion();
  const { ref, shown } = useInViewOnce();
  const Tag = as;

  if (reduce) {
    return <Tag className={className}>{children}</Tag>;
  }

  return (
    <Tag
      ref={ref as never}
      className={className}
      style={{
        opacity: shown ? 1 : 0,
        transform: shown ? "none" : "translateY(22px)",
        transition: `opacity ${DURATION_MS}ms ${EASE_CSS} ${delay}ms, transform ${DURATION_MS}ms ${EASE_CSS} ${delay}ms`,
        willChange: "opacity, transform",
      }}
    >
      {children}
    </Tag>
  );
}

/**
 * Stagger container. Wrap <Reveal> children — each is given an incremental delay
 * so they cascade in as the section enters view.
 */
export function Stagger({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  let index = 0;
  const items = Children.map(children, (child) => {
    if (isValidElement(child) && child.type === Reveal) {
      const base = (child.props as { delay?: number }).delay ?? 0;
      const withDelay = base + index * 90;
      index += 1;
      return cloneElement(child as ReactElement<{ delay?: number }>, { delay: withDelay });
    }
    return child;
  });
  return (
    <div className={className} style={style}>
      {items}
    </div>
  );
}
