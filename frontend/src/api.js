import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({ baseURL: API_BASE_URL });

export const getPlans = () => api.get('/plans');

export const searchProviders = (q) => api.get('/providers/search', { params: { q } });

export const searchBillingCodes = (q = '', billing_code_type) => {
    const params = { q };
    if (billing_code_type) params.billing_code_type = billing_code_type;
    return api.get('/billing_codes', { params });
};

export const getRateDistribution = (billing_code, billing_code_type = 'CPT', plan_name, setting, npi) => {
    const params = {};
    if (billing_code) { params.billing_code = billing_code; params.billing_code_type = billing_code_type; }
    if (plan_name) params.plan_name = plan_name;
    if (setting) params.setting = setting;
    if (npi) params.npi = npi;
    return api.get('/rates/distribution', { params });
};

export const getRatesByProvider = (billing_code, billing_code_type = 'CPT', plan_name, setting, npi, limit = 100) => {
    const params = { billing_code, billing_code_type, limit };
    if (plan_name) params.plan_name = plan_name;
    if (setting) params.setting = setting;
    if (npi) params.npi = npi;
    return api.get('/rates/providers', { params });
};

export default api;
