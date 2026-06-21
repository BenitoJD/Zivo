"use client";

import { useState } from "react";

type PageRangePickerProps = {
  pageCount: number;
  onConfirm: (range: { from: number; to: number }) => void;
  onCancel?: () => void;
};

export function PageRangePicker({ pageCount, onConfirm, onCancel }: PageRangePickerProps) {
  const [from, setFrom] = useState(1);
  const [to, setTo] = useState(Math.min(5, pageCount || 1));

  const safeTo = Math.min(to, pageCount || 1);
  const safeFrom = Math.min(from, safeTo);

  return (
    <div className="page-range-picker">
      <p className="page-range-picker__hint">
        Select pages to study ({pageCount || "?"} pages). MCQs and chat stay scoped to this range.
      </p>
      <div className="page-range-picker__thumbs">
        {Array.from({ length: pageCount || 0 }, (_, i) => i + 1).map((p) => (
          <button
            key={p}
            type="button"
            className={`page-thumb${p >= safeFrom && p <= safeTo ? " is-selected" : ""}`}
            onClick={() => {
              if (p < safeFrom) setFrom(p);
              else setTo(p);
            }}
          >
            {p}
          </button>
        ))}
      </div>
      <div className="page-range-picker__inputs">
        <label>
          From
          <input
            type="number"
            min={1}
            max={pageCount}
            value={safeFrom}
            onChange={(e) => setFrom(Number(e.target.value))}
          />
        </label>
        <label>
          To
          <input
            type="number"
            min={safeFrom}
            max={pageCount}
            value={safeTo}
            onChange={(e) => setTo(Number(e.target.value))}
          />
        </label>
      </div>
      <footer className="page-range-picker__bar">
        {onCancel && (
          <button type="button" className="page-range-picker__cancel" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button
          type="button"
          className="page-range-picker__confirm"
          onClick={() => onConfirm({ from: safeFrom, to: safeTo })}
        >
          Confirm range
        </button>
      </footer>
    </div>
  );
}
