"use client";

export function ModeSwitcher({
  mode,
  onChange,
}: {
  mode: "learn" | "test";
  onChange: (m: "learn" | "test") => void;
}) {
  return (
    <div className="mode-switcher">
      <button
        type="button"
        className={mode === "learn" ? "is-active" : ""}
        onClick={() => onChange("learn")}
      >
        Learn
      </button>
      <button
        type="button"
        className={mode === "test" ? "is-active" : ""}
        onClick={() => onChange("test")}
      >
        Test
      </button>
    </div>
  );
}
