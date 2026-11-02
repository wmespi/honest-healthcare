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

// Dataset context for the trust bar: { priceable_npis, networks, n_codes, as_of, ... }.
export const getHealth = () => api.get('/');

export const getPlans = (q = '') => api.get('/plans', { params: q ? { q } : {} });

// Structured network labels (from provider_references) — the reliable filter for
// a specific plan, e.g. "GA Blue Value HIX Individual Network". Returns
// [{ network_name, n_rates }].
export const getNetworks = (q = '') => api.get('/networks', { params: q ? { q } : {} });

// The plan versions on a network, with their sourced cost sharing.
export const getPlanCostSharing = (network_name) =>
  api.get('/plans/cost_sharing', { params: { network_name } });

export const getImagingProviders = (billing_code, network_name, near) =>
  api.get('/imaging/providers', { params: { billing_code, network_name, ...(near || {}) } });

// The curated service lines: { [name]: { taxonomy_codes, billing_codes, menu } }.
export const getServiceLines = () => api.get('/service_lines');

export default api;
