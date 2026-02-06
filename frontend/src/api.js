import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
    baseURL: API_BASE_URL,
});

export const getHospitals = () => api.get('/hospitals');
export const getPayers = () => api.get('/payers');
export const getPlans = (payer) => api.get('/plans', { params: { payer } });

export const getRates = (search, hospital, setting, payer, plan) => {
    const params = {};
    if (search) params.search = search;
    if (hospital) params.hospital = hospital;
    if (setting) params.setting = setting;
    if (payer) params.payer = payer;
    if (plan) params.plan = plan;
    return api.get('/rates', { params });
};

export const getProcedures = (search, hospital, setting, payer, plan) => {
    const params = {};
    if (search) params.search = search;
    if (hospital) params.hospital = hospital;
    if (setting) params.setting = setting;
    if (payer) params.payer = payer;
    if (plan) params.plan = plan;
    return api.get('/procedures', { params });
};

export default api;
