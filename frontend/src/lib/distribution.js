// Computes a clean step size so the x-axis reads $0, $50, $100... or $0, $5, $10...
export function niceStep(maxVal) {
  if (maxVal <= 0) return 1;
  const rough = maxVal / 12;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  if (norm <= 1) return mag;
  if (norm <= 2) return 2 * mag;
  if (norm <= 5) return 5 * mag;
  return 10 * mag;
}

// Always starts at $0, uses clean intervals, includes empty buckets so the axis is continuous.
export function bucketDistribution(distribution) {
  if (!distribution || distribution.length === 0) return [];
  const rates = distribution.map(d => d.rate);
  const dataMax = Math.max(...rates);
  const step = niceStep(dataMax);
  const numBuckets = Math.floor(dataMax / step) + 1;

  const buckets = Array.from({ length: numBuckets }, (_, i) => ({
    low: i * step,
    high: (i + 1) * step,
    provider_groups: 0,
  }));

  for (const d of distribution) {
    const idx = Math.min(Math.floor(d.rate / step), numBuckets - 1);
    if (idx >= 0) buckets[idx].provider_groups += d.provider_groups;
  }

  return buckets.map(b => ({
    label: `$${Math.round(b.low)}`,
    rate_mid: (b.low + b.high) / 2,
    provider_groups: b.provider_groups,
  }));
}
