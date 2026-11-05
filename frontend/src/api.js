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

// The find-care screen's routes, in serving/contract.py's shape: every plan, every
// service, and one service on one plan near one ZIP code.
export const getPlans = () => api.get('/plans');
export const getServices = () => api.get('/services');
export const getFindCare = ({ serviceId, planId, zip, radiusMi, sort }) => api.get('/find-care', {
  params: { service_id: serviceId, plan_id: planId, zip, ...(radiusMi ? { radius_mi: radiusMi } : {}), sort },
});

export default api;
