"use client";

import { type ReactNode } from "react";
import { motion, useReducedMotion, type Variants } from "framer-motion";

/**
 * Shared motion primitives for the landing page.
 *
 * All animation reuses the shell's easing (cubic-bezier(0.32, 0.72, 0, 1)) so the
 * landing feels continuous with the workspace. Everything is gated on
 * prefers-reduced-motion — when reduced, reveal/stagger render instantly.
 */

/** Matches SHELL_EASE in Sidebar.tsx / workspace/layout.tsx. */
export const EASE = [0.32, 0.72, 0, 1] as const;

export const DURATION_MS = 0.56;

/** Standard lift-in variant for scroll reveals. */
export const revealVariants: Variants = {
  hidden: { opacity: 0, y: 18 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION_MS, ease: EASE },
  },
};

/** Container that staggers its <Reveal> children. */
export const staggerVariants: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.085, delayChildren: 0.04 },
  },
};

/**
 * Single scroll-triggered reveal. Plays once when it enters the viewport.
 * Falls back to a plain fragment when reduced-motion is requested.
 */
export function Reveal({
  children,
  className,
  as = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "span" | "li";
}) {
  const reduce = useReducedMotion();
  if (reduce) return <>{children}</>;
  const MotionTag = motion[as];
  return (
    <MotionTag
      className={className}
      variants={revealVariants}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: "-10% 0px -10% 0px" }}
    >
      {children}
    </MotionTag>
  );
}

/**
 * Stagger container. Pair with <Reveal> children. Triggers when scrolled into view.
 */
export function Stagger({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className} style={style}>{children}</div>;
  return (
    <motion.div
      className={className}
      style={style}
      variants={staggerVariants}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: "-10% 0px -10% 0px" }}
    >
      {children}
    </motion.div>
  );
}
