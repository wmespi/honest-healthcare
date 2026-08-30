import axios from 'axios';

// The API runs on port 8000 of the same host that served this page. Derive it at
// runtime from window.location so one build works whether the page was opened via
// localhost, a LAN IP, or a Tailscale MagicDNS name — no per-host rebuild.
// VITE_API_URL still overrides (split deployments, a proxied /api path).
const runtimeApiUrl =
  typeof window !== 'undefined' && window.location?.hostname
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : 'http://localhost:8000';

const API_BASE_URL = import.meta.env.VITE_API_URL || runtimeApiUrl;

const api = axios.create({ baseURL: API_BASE_URL });

export const getPlans = (q = '') => api.get('/plans', { params: q ? { q } : {} });

// Structured network labels (from provider_references) — the reliable filter for
// a specific plan, e.g. "GA Blue Value HIX Individual Network". Returns
// [{ network_name, n_rates }].
export const getNetworks = (q = '') => api.get('/networks', { params: q ? { q } : {} });

export const searchProviders = (q, specialty) =>
    api.get('/providers/search', { params: { q: q || '', ...(specialty ? { specialty } : {}) } });

// NUCC specialties we hold GA providers for — the "by specialty" search mode.
export const getSpecialties = (q = '') =>
    api.get('/specialties', { params: q ? { q } : {} });

export const searchBillingCodes = (q = '', billing_code_type) => {
    const params = { q };
    if (billing_code_type) params.billing_code_type = billing_code_type;
    return api.get('/billing_codes', { params });
};

// RBCS categories present in the data: [{ category, subcategory, n_codes, provider_groups }].
export const getProcedureCategories = () => api.get('/procedure_categories');

// The provider "menu" — every procedure this NPI has a negotiated rate for, with
// the rate range. Returns { npi, count, results: [{ billing_code, label,
// rbcs_category, min_rate, median_rate, max_rate, n_rates }] }.
export const getProviderMenu = (npi, network_name, setting, q = '', tier = 'plausible') => {
    const params = {};
    if (network_name) params.network_name = network_name;
    if (setting) params.setting = setting;
    if (q) params.q = q;
    if (tier && tier !== 'plausible') params.tier = tier;
    return api.get(`/providers/${npi}/procedures`, { params });
};

export const getRateDistribution = (billing_code, billing_code_type = 'CPT', network_name, setting, npi) => {
    const params = {};
    if (billing_code) { params.billing_code = billing_code; params.billing_code_type = billing_code_type; }
    if (network_name) params.network_name = network_name;
    if (setting) params.setting = setting;
    if (npi) params.npi = npi;
    return api.get('/rates/distribution', { params });
};

// One row per contracted provider group for a code, ordered by price, plus a
// summary over every matching group. Powers the "compare across providers" table.
// Returns { billing_code, summary: {min,median,avg,max,n_groups,n_providers}, results: [...] }.
// Job 1 — cost for one procedure at one provider, organised by component
// (global / professional -26 / technical -TC) and place of service.
// Returns { headline: {rate, max_rate, basis, pos_label}, components: [...], is_component_split }.
export const getRateQuote = (billing_code, billing_code_type, npi, network_name) => {
    const params = { billing_code, npi };
    if (billing_code_type) params.billing_code_type = billing_code_type;
    if (network_name) params.network_name = network_name;
    return api.get('/rates/quote', { params });
};

// Job 2 — same procedure across every network. Returns { networks: [{ network_name,
// median, min, max, typical_low, typical_high, spread, n_groups }] }, sorted by median.
export const getRatesByNetwork = (billing_code, billing_code_type = 'CPT', setting) => {
    const params = { billing_code, billing_code_type };
    if (setting) params.setting = setting;
    return api.get('/rates/by_network', { params });
};

export const getRatesByProvider = (billing_code, billing_code_type = 'CPT', network_name, setting, npi, { component = 'global' } = {}) => {
    const params = { billing_code, billing_code_type, component };
    if (network_name) params.network_name = network_name;
    if (setting) params.setting = setting;
    if (npi) params.npi = npi;
    return api.get('/rates/providers', { params });
};

export default api;
