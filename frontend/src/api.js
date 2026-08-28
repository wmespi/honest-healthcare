import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({ baseURL: API_BASE_URL });

export const getPlans = (q = '') => api.get('/plans', { params: q ? { q } : {} });

// Structured network labels (from provider_references) — the reliable filter for
// a specific plan, e.g. "GA Blue Value HIX Individual Network". Returns
// [{ network_name, n_rates }].
export const getNetworks = (q = '') => api.get('/networks', { params: q ? { q } : {} });

export const searchProviders = (q) => api.get('/providers/search', { params: { q } });

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
export const getProviderMenu = (npi, network_name, setting, q = '') => {
    const params = {};
    if (network_name) params.network_name = network_name;
    if (setting) params.setting = setting;
    if (q) params.q = q;
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

export const getRatesByProvider = (billing_code, billing_code_type = 'CPT', network_name, setting, npi, { sort = 'rate_asc', limit = 200 } = {}) => {
    const params = { billing_code, billing_code_type, sort, limit };
    if (network_name) params.network_name = network_name;
    if (setting) params.setting = setting;
    if (npi) params.npi = npi;
    return api.get('/rates/providers', { params });
};

export default api;
