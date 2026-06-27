"use client";

/**
 * A tiny, cute loading character — a "sprout-blob" that bobs, blinks, squashes,
 * and sways its leaf. On-brand for a learning app (something growing) and built to
 * feel premium, not toy-like. Pure CSS keyframes on GPU-friendly transforms; honors
 * prefers-reduced-motion (renders a calm static pose). Drop it anywhere a wait shows.
 */

type Variant = "lavender" | "sage" | "terracotta";

const TINT: Record<Variant, { body: string; belly: string; cheek: string }> = {
  lavender: {
    body: "var(--mantine-color-lavender-4)",
    belly: "var(--mantine-color-lavender-2)",
    cheek: "var(--mantine-color-lavender-6)",
  },
  sage: {
    body: "var(--mantine-color-sage-4)",
    belly: "var(--mantine-color-sage-2)",
    cheek: "var(--mantine-color-sage-6)",
  },
  terracotta: {
    body: "var(--mantine-color-terracotta-4)",
    belly: "var(--mantine-color-terracotta-2)",
    cheek: "var(--mantine-color-terracotta-6)",
  },
};

const PET_CSS = `
@keyframes zv-pet-bob { 0%,100% { transform: translateY(2%); } 50% { transform: translateY(-7%); } }
@keyframes zv-pet-squash { 0%,100% { transform: scale(1,1); } 50% { transform: scale(0.985,1.03); } }
@keyframes zv-pet-blink { 0%,90%,100% { transform: scaleY(1); } 95% { transform: scaleY(0.08); } }
@keyframes zv-pet-shadow { 0%,100% { transform: scaleX(1); opacity: 0.32; } 50% { transform: scaleX(0.78); opacity: 0.18; } }
@keyframes zv-pet-leaf { 0%,100% { transform: rotate(-5deg); } 50% { transform: rotate(6deg); } }
@keyframes zv-pet-spark { 0% { transform: translateY(0) scale(0.6); opacity: 0; } 30% { opacity: 1; } 100% { transform: translateY(-130%) scale(1); opacity: 0; } }
.zv-pet-bob { animation: zv-pet-bob 2.6s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
.zv-pet-squash { animation: zv-pet-squash 2.6s ease-in-out infinite; transform-box: fill-box; transform-origin: 50% 100%; }
.zv-pet-eye { animation: zv-pet-blink 4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
.zv-pet-shadow { animation: zv-pet-shadow 2.6s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
.zv-pet-leaf { animation: zv-pet-leaf 3.4s ease-in-out infinite; transform-box: fill-box; transform-origin: 50% 100%; }
.zv-pet-spark { animation: zv-pet-spark 2.8s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
@media (prefers-reduced-motion: reduce) {
  .zv-pet-bob, .zv-pet-squash, .zv-pet-eye, .zv-pet-shadow, .zv-pet-leaf, .zv-pet-spark { animation: none !important; }
}
`;

export function PetLoader({
  size = 72,
  variant = "lavender",
  label,
}: {
  size?: number;
  variant?: Variant;
  label?: string;
}) {
  const t = TINT[variant];
  return (
    <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "center", gap: label ? 12 : 0 }}>
      <style>{PET_CSS}</style>
      <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label={label || "Loading"} style={{ overflow: "visible" }}>
        {/* ground shadow */}
        <ellipse className="zv-pet-shadow" cx="50" cy="90" rx="24" ry="5" fill="var(--mantine-color-gray-6)" />
        {/* drifting spark */}
        <circle className="zv-pet-spark" cx="72" cy="40" r="2.4" fill={t.cheek} style={{ animationDelay: "0.6s" }} />
        <circle className="zv-pet-spark" cx="28" cy="46" r="1.8" fill={t.cheek} style={{ animationDelay: "1.8s" }} />
        <g className="zv-pet-bob">
          <g className="zv-pet-squash">
            {/* leaf sprout */}
            <g className="zv-pet-leaf">
              <path d="M50 26 C50 14, 60 10, 66 8 C64 18, 58 24, 50 26 Z" fill="var(--mantine-color-sage-5)" />
              <line x1="50" y1="30" x2="50" y2="22" stroke="var(--mantine-color-sage-6)" strokeWidth="2" strokeLinecap="round" />
            </g>
            {/* body */}
            <path
              d="M50 28 C70 28, 82 42, 82 60 C82 78, 68 88, 50 88 C32 88, 18 78, 18 60 C18 42, 30 28, 50 28 Z"
              fill={t.body}
            />
            {/* belly highlight */}
            <ellipse cx="50" cy="66" rx="20" ry="16" fill={t.belly} opacity="0.7" />
            {/* cheeks */}
            <circle cx="33" cy="64" r="5" fill={t.cheek} opacity="0.35" />
            <circle cx="67" cy="64" r="5" fill={t.cheek} opacity="0.35" />
            {/* eyes */}
            <ellipse className="zv-pet-eye" cx="41" cy="56" rx="3.4" ry="4.6" fill="#2A2620" style={{ animationDelay: "0.1s" }} />
            <ellipse className="zv-pet-eye" cx="59" cy="56" rx="3.4" ry="4.6" fill="#2A2620" />
            {/* eye glints */}
            <circle cx="42.2" cy="54.4" r="1" fill="#fff" />
            <circle cx="60.2" cy="54.4" r="1" fill="#fff" />
            {/* smile */}
            <path d="M45 70 Q50 74, 55 70" fill="none" stroke="#2A2620" strokeWidth="1.8" strokeLinecap="round" opacity="0.75" />
          </g>
        </g>
      </svg>
      {label ? (
        <span style={{ fontSize: 13, color: "var(--mantine-color-dimmed)", fontWeight: 500, textAlign: "center" }}>{label}</span>
      ) : null}
    </div>
  );
}
