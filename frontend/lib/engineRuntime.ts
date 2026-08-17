/** If-token-free decision runtime. Engines are rule tables; callers apply(). */

export type Pred = {
  key: string;
  op?: string;
  value?: unknown;
};

export type Rule = {
  when: Pred[];
  action: string;
  rationale?: string;
  extras?: Record<string, unknown>;
};

const OPS: Record<string, (a: unknown, b: unknown) => boolean> = {
  lt: (a, b) => Number(a) < Number(b),
  lte: (a, b) => Number(a) <= Number(b),
  gt: (a, b) => Number(a) > Number(b),
  gte: (a, b) => Number(a) >= Number(b),
  eq: (a, b) => a === b,
  neq: (a, b) => a !== b,
  truthy: (a) => Boolean(a),
  falsey: (a) => !a,
  contains: (a, b) => Boolean(Array.isArray(a) && a.includes(b)),
  in_set: (a, b) => Boolean(Array.isArray(b) && b.includes(a)),
};

function predOk(pred: Pred, signals: Record<string, unknown>): boolean {
  const op = OPS[pred.op ?? "truthy"];
  return op(signals[pred.key], pred.value);
}

export function firstMatch(
  rules: Rule[],
  signals: Record<string, unknown>,
  fallback: Rule = { when: [], action: "unmatched" },
): Rule {
  return rules.find((rule) => rule.when.every((pred) => predOk(pred, signals))) ?? fallback;
}

export function apply<T>(action: string, handlers: Record<string, () => T>): T {
  return handlers[action]();
}

/** Eager: both branches evaluate. Use pick when a branch may throw. */
export function choose<A, B>(flag: boolean, whenTrue: A, whenFalse: B): A | B {
  return ({ true: whenTrue, false: whenFalse } as Record<string, A | B>)[String(Boolean(flag))];
}

export function pick<A, B>(flag: boolean, whenTrue: () => A, whenFalse: () => B): A | B {
  return ({ true: whenTrue, false: whenFalse } as Record<string, () => A | B>)[String(Boolean(flag))]();
}

export function errorMessage(err: unknown, fallback: string): string {
  return pick(err instanceof Error, () => (err as Error).message, () => fallback);
}
