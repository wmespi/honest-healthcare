// Shared dollar-amount formatter — every rate/estimate view uses this.
export const fmt = (n) =>
  n == null ? '-' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
