// Short, plan-type-aware label for the long MRF network names.
export function shortNetwork(name) {
  if (!name) return name;
  if (/blue value/i.test(name)) return 'Blue Value (HMO)';
  if (/traditional/i.test(name)) return 'Traditional (PPO)';
  if (/\bHBP\b/i.test(name)) return 'HBP Specialties';
  return name.replace(/^GA\s+/, '');
}
