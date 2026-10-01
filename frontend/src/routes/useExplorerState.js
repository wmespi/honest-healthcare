import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { getRateDistribution, getRatesByProvider, getRatesByNetwork, getRateQuote, getProviderMenu, searchBillingCodes, searchProviders, getServiceLines } from '../api';
import { planIsConfigured } from '../oop';
import { usePlanParams } from '../usePlanParams';
import { readDeepLink, buildDeepLinkQuery } from '../lib/deepLink';
import { cleanProcedureName } from '../lib/procedureNames';
import { bucketDistribution } from '../lib/distribution';

// All of Explorer's state, data-fetching effects, and handlers — split out of
// routes/Explorer.jsx (which is purely presentational) so each file stays a
// readable size. `lockedServiceLine` is the one thing the route (not the user)
// fixes for the component's whole lifetime (#87) — see Explorer.jsx's routing.
const DEFAULT_RADIUS_MI = 10;

export function useExplorerState(lockedServiceLine) {
  const navigate = useNavigate();

  // Shareable deep link — ?plan=&specialty=&npi=&code=&type= — read once on
  // mount and used to seed the state below, so a journey URL lands straight on
  // its endpoint instead of the plan gate. Cheap + pure; recomputing it on every
  // render is fine since only the initial useState value ever reads it.
  const deepLink = readDeepLink();

  const [selectedPlan, setSelectedPlan] = useState(() => {
    if (deepLink.plan) return deepLink.plan;
    try { return localStorage.getItem('hh_network_v1') || ''; } catch { return ''; }
  });
  // Drops the plan gate for "just browse the data" — distinct from having a plan.
  const [bypassGate, setBypassGate] = useState(deepLink.bypass);
  const gated = !selectedPlan && !bypassGate;

  useEffect(() => {
    try {
      if (selectedPlan) localStorage.setItem('hh_network_v1', selectedPlan);
      else localStorage.removeItem('hh_network_v1');
    } catch { /* private mode — the plan just won't persist */ }
  }, [selectedPlan]);

  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  // Setting filter retired: the rate views are outpatient-professional by scope
  // now (serving/data_sources.outpatient_scope). A place-of-service split
  // (office vs hospital-outpatient) is the future refinement.
  const setting = '';
  const [specialty, setSpecialtyState] = useState(deepLink.specialty);
  // An exact taxonomy-code scope (#83) — the coarse-testing sibling of
  // `specialty`'s fuzzy text match; mutually exclusive with it in the UI.
  // Route-driven (#87), not user-togglable: whichever route mounted this
  // component fixes it for the component's lifetime — "leaving" the scope
  // means navigating to /explore, not clearing a filter.
  const serviceLine = lockedServiceLine || '';
  const [npi, setNpi] = useState(deepLink.npi);
  // Where the user is, for the PCP picker's distance ranking. The ZIP only
  // reaches the API once it is a full 5 digits.
  const [zip, setZipState] = useState(deepLink.zip);
  const [radiusMi, setRadiusMi] = useState(Number(deepLink.radius) || DEFAULT_RADIUS_MI);
  const near = /^\d{5}$/.test(zip) ? { zip, radius_mi: radiusMi } : undefined;
  const [npiLabel, setNpiLabel] = useState('');

  // The curated service-line billing-code allowlists (#83, #100) — fetched
  // once from GET /service_lines instead of a hand-synced frontend copy.
  // { [name]: { taxonomy_codes, billing_codes } }; empty until it loads, which
  // only briefly widens the menu/search scope on first mount.
  const [serviceLineCodes, setServiceLineCodes] = useState({});
  useEffect(() => { getServiceLines().then(r => setServiceLineCodes(r.data || {})).catch(() => {}); }, []);
  const activeCodeScope = serviceLine ? serviceLineCodes[serviceLine]?.billing_codes : undefined;

  const [distribution, setDistribution] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedCode, setSelectedCode] = useState(() =>
    deepLink.code ? { code: deepLink.code, type: deepLink.type } : null
  );
  const [error, setError] = useState(null);

  const [providerRates, setProviderRates] = useState(null);
  const [providerRatesLoading, setProviderRatesLoading] = useState(false);

  const [providerMenu, setProviderMenu] = useState(null);
  const [providerMenuLoading, setProviderMenuLoading] = useState(false);
  const [menuTier, setMenuTier] = useState('plausible'); // 'plausible' | 'all'
  const planParams = usePlanParams();
  const planConfigured = planIsConfigured(planParams.plan);

  const [providerQuote, setProviderQuote] = useState(null);
  const [providerQuoteLoading, setProviderQuoteLoading] = useState(false);

  const [networkCompare, setNetworkCompare] = useState(null);
  const [networkCompareLoading, setNetworkCompareLoading] = useState(false);
  // Collapsed until asked — see the NetworkCompare comment. Re-collapses on a
  // new procedure so it's never showing stale data from the last one.
  const [networkCompareExpanded, setNetworkCompareExpanded] = useState(false);
  useEffect(() => { setNetworkCompareExpanded(false); }, [selectedCode?.code]);

  const [specialtyProviders, setSpecialtyProviders] = useState(null);
  const [specialtyProvidersLoading, setSpecialtyProvidersLoading] = useState(false);

  // Load the overview once a plan is in play — either restored from a previous
  // visit (on mount) or after the gate is dismissed. Picking a plan fetches via
  // handlePlanSelect, so this must not also fire on selectedPlan. Seeds from a
  // deep link's code/npi/specialty on the first run (their initial state), so
  // e.g. ?code=99213&npi=... lands directly on that quote.
  useEffect(() => {
    if (gated) return;
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan || undefined, '', npi, selectedCode?.rbcs_category, specialty);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bypassGate]);

  // Keep the address bar a shareable link to the current selection —
  // history.replaceState (not react-router's navigate) so this doesn't push a
  // history entry per keystroke/pick and the back button isn't spammed; the
  // *route* (set by react-router) is untouched, only the query string.
  // Powers the per-journey URLs in docs/journeys.md.
  useEffect(() => {
    const qs = buildDeepLinkQuery({
      plan: selectedPlan, specialty, npi, bypass: bypassGate,
      code: selectedCode?.code, type: selectedCode?.type,
      zip: near?.zip, radius: near && radiusMi !== DEFAULT_RADIUS_MI ? radiusMi : undefined,
    });
    const url = window.location.pathname + (qs ? `?${qs}` : '');
    window.history.replaceState(null, '', url);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPlan, specialty, npi, selectedCode, bypassGate, near?.zip, radiusMi]);

  // Ranked provider list for the chosen specialty OR service line (#83) — the
  // step between "pick your care" and a specific provider. Only when one scope
  // is set with no provider or procedure yet; mutually exclusive with each
  // other in the UI, so at most one is ever truthy here.
  useEffect(() => {
    if ((!specialty && !serviceLine) || npi || selectedCode?.code) { setSpecialtyProviders(null); return; }
    let cancelled = false;
    setSpecialtyProvidersLoading(true);
    searchProviders('', specialty, 40, selectedPlan || undefined, serviceLine, near)
      .then(res => { if (!cancelled) setSpecialtyProviders(res.data); })
      .catch(() => { if (!cancelled) setSpecialtyProviders(null); })
      .finally(() => { if (!cancelled) setSpecialtyProvidersLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specialty, serviceLine, npi, selectedCode, selectedPlan, near?.zip, radiusMi]);

  // Compare-across-providers table — a code is chosen, NO provider filter, and a
  // plan is picked (rates are plan-specific; the endpoint requires a network).
  useEffect(() => {
    const code = selectedCode?.code;
    if (!code || npi || !selectedPlan) { setProviderRates(null); return; }
    let cancelled = false;
    setProviderRatesLoading(true);
    getRatesByProvider(code, selectedCode.type, selectedPlan, setting || undefined, undefined,
      { specialty: specialty || undefined })
      .then(res => { if (!cancelled) setProviderRates(res.data); })
      .catch(() => { if (!cancelled) setProviderRates(null); })
      .finally(() => { if (!cancelled) setProviderRatesLoading(false); });
    return () => { cancelled = true; };
  }, [selectedCode, selectedPlan, setting, npi, specialty]);

  // Job 2 — compare the selected procedure across every network. Deferred
  // until NetworkCompare is expanded (the /rates/by_network scan runs ~10s
  // on the full corpus — no reason to pay that on every procedure page load).
  useEffect(() => {
    const code = selectedCode?.code;
    if (!code || !networkCompareExpanded) { setNetworkCompare(null); return; }
    let cancelled = false;
    setNetworkCompareLoading(true);
    getRatesByNetwork(code, selectedCode.type, setting || undefined)
      .then(res => { if (!cancelled) setNetworkCompare(res.data); })
      .catch(() => { if (!cancelled) setNetworkCompare(null); })
      .finally(() => { if (!cancelled) setNetworkCompareLoading(false); });
    return () => { cancelled = true; };
  }, [selectedCode, setting, networkCompareExpanded]);

  // Job 1 cost card — a provider AND a procedure are selected AND a plan is
  // picked (the quote is plan-specific; the endpoint requires a network).
  useEffect(() => {
    const code = selectedCode?.code;
    if (!npi || !code || !selectedPlan) { setProviderQuote(null); return; }
    let cancelled = false;
    setProviderQuoteLoading(true);
    getRateQuote(code, selectedCode.type, npi, selectedPlan)
      .then(res => { if (!cancelled) setProviderQuote(res.data); })
      .catch(() => { if (!cancelled) setProviderQuote(null); })
      .finally(() => { if (!cancelled) setProviderQuoteLoading(false); });
    return () => { cancelled = true; };
  }, [npi, selectedCode, selectedPlan]);

  // Provider "menu" — every procedure the selected provider has a rate for.
  // Shown only when a provider is chosen but no specific procedure is.
  // Reset to the plausible view whenever the provider / plan / setting changes.
  useEffect(() => { setMenuTier('plausible'); }, [npi, selectedPlan, setting]);

  useEffect(() => {
    if (!npi || selectedCode?.code) { setProviderMenu(null); return; }
    let cancelled = false;
    setProviderMenuLoading(true);
    getProviderMenu(npi, selectedPlan || undefined, setting || undefined, '', menuTier)
      .then(res => { if (!cancelled) setProviderMenu(res.data); })
      .catch(() => { if (!cancelled) setProviderMenu(null); })
      .finally(() => { if (!cancelled) setProviderMenuLoading(false); });
    return () => { cancelled = true; };
  }, [npi, selectedCode, selectedPlan, setting, menuTier]);

  // Procedure search. When a provider is selected, scope suggestions to that
  // provider's actual menu (via /providers/{npi}/procedures) so we never offer
  // a procedure the provider doesn't have. Otherwise search the full catalog.
  useEffect(() => {
    if (!isFocused) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }
    const delay = query.length === 0 ? 0 : 300;
    const timer = setTimeout(() => {
      // scoped to the active service line (#83), same as the menu view itself
      const codeScope = activeCodeScope || null;
      const req = npi
        ? getProviderMenu(npi, selectedPlan || undefined, setting || undefined, query)
            .then(res => (res.data.results || [])
              .filter(r => !codeScope || codeScope.includes(r.billing_code))
              .slice(0, 20).map(r => ({
                billing_code: r.billing_code,
                billing_code_type: r.billing_code_type,
                label: r.label,
                rbcs_subcategory: r.rbcs_subcategory,
                min_rate: r.min_rate,
                max_rate: r.max_rate,
                n_rates: r.n_rates,
              })))
        : searchBillingCodes(query).then(res => res.data);
      req
        .then(list => { setSuggestions(list); setShowSuggestions(true); })
        .catch(() => {});
    }, delay);
    return () => clearTimeout(timer);
  }, [query, isFocused, npi, selectedPlan, setting, serviceLine, activeCodeScope]);

  const fetchDistribution = useCallback(async (code, type, planName, activeSetting, activeNpi, rbcsCategory, activeSpecialty) => {
    // No code yet, but a provider, a specialty, or a locked service line (#83)
    // is chosen: those are the "menu" / "specialty provider list" views, each
    // handled by its own effect. Calling /rates/distribution here would scan
    // prices with nothing pruning the code axis (and a network+specialty
    // codeless scan is the slow path) — skip it. `serviceLine` is read from
    // the outer closure, not a parameter — it's a route-derived constant for
    // the component's whole lifetime (#87), safe to close over, and must
    // never be forwarded as `activeSpecialty` below: it's a taxonomy-code-
    // family slug ("pcp"), not a real NUCC specialty label, and sending it as
    // `specialty=` 404s a query that has real data (found live-testing #87 —
    // picking a code while a service line was locked broke every quote).
    if (!code && (activeNpi || activeSpecialty || serviceLine)) {
      setDistribution(null);
      setSelectedCode(null);
      setError(null);
      setLoading(false);
      setShowSuggestions(false);
      return;
    }
    setLoading(true);
    setError(null);
    setShowSuggestions(false);
    try {
      const res = await getRateDistribution(code || undefined, type || undefined, planName || undefined, activeSetting || undefined, activeNpi || undefined, activeSpecialty || undefined);
      setDistribution(res.data);
      setSelectedCode(code ? { code, type, rbcs_category: rbcsCategory } : null);
    } catch (err) {
      setDistribution(null);
      if (err.response?.status === 404) {
        const scope = planName || 'this network';
        setError(
          activeNpi && !code ? `No negotiated rates for this provider in ${scope}.`
          : activeNpi && code ? `No rates for ${type} ${code} at this provider in ${scope}.`
          : code ? `No rates found for ${type} ${code} in ${scope}.`
          : `No rates found in ${scope}.`
        );
      } else {
        setError('Query failed');
      }
    } finally {
      setLoading(false);
    }
  }, [serviceLine]);

  const handleSuggestionClick = (sug) => {
    setQuery(sug.label || cleanProcedureName(sug.name) || `${sug.billing_code} (${sug.billing_code_type})`);
    setShowSuggestions(false);
    fetchDistribution(sug.billing_code, sug.billing_code_type, selectedPlan, setting, npi, sug.rbcs_category, specialty);
  };

  const handlePlanSelect = (plan) => {
    setSelectedPlan(plan);
    fetchDistribution(selectedCode?.code, selectedCode?.type, plan, setting, npi, undefined, specialty);
  };

  const handleSpecialtyChange = (sp) => {
    setSpecialtyState(sp);
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, setting, npi, undefined, sp);
  };

  const handleNpiSelect = (n, label) => {
    setNpi(n);
    setNpiLabel(label || '');
    // A specific provider supersedes the free-text specialty filter — clear it
    // so they can't contradict (a speech therapist under a "Psychiatry" chip).
    // `serviceLine` is different: it stays active once a provider is picked, so
    // the procedure menu can scope to it (#83) — it's "what we're shopping
    // for", not just a search-narrowing filter to discard on pick.
    if (n && specialty) setSpecialtyState('');
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, setting, n, undefined, n ? '' : specialty);
  };

  const handleCategoryPick = (term) => {
    setQuery(term);
    setIsFocused(true);
    setShowSuggestions(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // Drill from a provider-menu row into that procedure's full breakdown.
  const handleMenuPick = (row) => {
    setQuery(row.label || `${row.billing_code} (${row.billing_code_type})`);
    fetchDistribution(row.billing_code, row.billing_code_type, selectedPlan, setting, npi, row.rbcs_category, specialty);
  };

  const buckets = distribution ? bucketDistribution(distribution.distribution) : [];
  const summary = distribution?.summary;

  const medianBucket = summary && buckets.length > 0
    ? buckets.reduce((prev, curr) =>
        Math.abs(curr.rate_mid - summary.median) < Math.abs(prev.rate_mid - summary.median) ? curr : prev,
        buckets[0])
    : null;

  return {
    navigate,
    selectedPlan, setSelectedPlan, bypassGate, setBypassGate, gated,
    query, setQuery, suggestions, showSuggestions, setShowSuggestions, isFocused, setIsFocused,
    specialty, serviceLine, npi, npiLabel, activeCodeScope,
    zip, setZip: setZipState, radiusMi, setRadiusMi, near,
    distribution, loading, selectedCode, error,
    providerRates, providerRatesLoading,
    providerMenu, providerMenuLoading, setMenuTier,
    planParams, planConfigured,
    providerQuote, providerQuoteLoading,
    networkCompare, networkCompareLoading, networkCompareExpanded, setNetworkCompareExpanded,
    specialtyProviders, specialtyProvidersLoading,
    buckets, summary, medianBucket,
    handleSuggestionClick, handlePlanSelect, handleSpecialtyChange, handleNpiSelect,
    handleCategoryPick, handleMenuPick,
  };
}
