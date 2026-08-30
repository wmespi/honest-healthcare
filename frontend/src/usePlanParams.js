import { useCallback, useEffect, useState } from 'react';

const KEY = 'hh_plan_v1';
const EMPTY = { deductibleTotal: '', deductibleMet: '', coinsurance: '', oopMax: '', oopMet: '', copays: {} };

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const p = JSON.parse(raw);
    return { ...EMPTY, ...p, copays: { ...(p.copays || {}) } };
  } catch {
    return EMPTY;
  }
}

// Coerce the stringy form state into the numeric shape oop.js wants.
export function toPlan(form) {
  const num = (v) => {
    const x = parseFloat(v);
    return isFinite(x) && x >= 0 ? x : undefined;
  };
  const copays = {};
  for (const [k, v] of Object.entries(form.copays || {})) {
    const x = num(v);
    if (x != null) copays[k] = x;
  }
  return {
    deductibleTotal: num(form.deductibleTotal),
    deductibleMet: num(form.deductibleMet),
    coinsurance: num(form.coinsurance),
    oopMax: num(form.oopMax),
    oopMet: num(form.oopMet),
    copays,
  };
}

/** Stringy form state for the plan panel, persisted to localStorage. */
export function usePlanParams() {
  const [form, setForm] = useState(load);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(form));
    } catch {
      /* private mode / disabled storage — estimates just won't persist */
    }
  }, [form]);

  const setField = useCallback((k, v) => setForm((f) => ({ ...f, [k]: v })), []);
  const setCopay = useCallback(
    (k, v) => setForm((f) => ({ ...f, copays: { ...f.copays, [k]: v } })),
    [],
  );
  const clear = useCallback(() => setForm(EMPTY), []);

  return { form, setField, setCopay, clear, plan: toPlan(form) };
}
