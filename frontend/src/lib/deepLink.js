// Shareable deep links into the rate explorer — ?plan=&specialty=&npi=&code=&type=
// lands straight on a specific quote instead of the plan gate. Built for
// docs/journeys.md: one clickable URL per journey to sanity-check the live app.
// Query-param based, so it composes with whichever route you're on (#87) —
// `Explorer` reads these once on mount and keeps the address bar in sync via
// history.replaceState as the selection changes. `service_line` (#83) is no
// longer part of this: it's route-driven now (/find-care/pcp), not a query
// param — see App.jsx's <Routes>.

export function readDeepLink() {
  if (typeof window === 'undefined') {
    return { plan: '', specialty: '', npi: '', code: '', type: 'CPT', bypass: false };
  }
  const p = new URLSearchParams(window.location.search);
  return {
    plan: p.get('plan') || '',
    specialty: p.get('specialty') || '',
    npi: p.get('npi') || '',
    code: p.get('code') || '',
    type: p.get('type') || 'CPT',
    // ?bypass=1 links the no-plan "browsing" view (J4) — otherwise a bare URL
    // with no plan just shows the gate, same as a first-time visit.
    bypass: p.get('bypass') === '1',
  };
}

// Builds the query string for the current selection — the inverse of
// readDeepLink. `type` is omitted when it's the default (CPT), to keep the
// common-case URL short.
export function buildDeepLinkQuery({ plan, specialty, npi, code, type, bypass }) {
  const p = new URLSearchParams();
  if (plan) p.set('plan', plan);
  else if (bypass) p.set('bypass', '1');
  if (specialty) p.set('specialty', specialty);
  if (npi) p.set('npi', npi);
  if (code) {
    p.set('code', code);
    if (type && type !== 'CPT') p.set('type', type);
  }
  return p.toString();
}
