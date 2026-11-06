import axios from 'axios';

// The API is under /api on the origin that served this page (application/serving/main.py),
// so one build works at any address: localhost, a tailnet name or the deployed URL.
const api = axios.create({ baseURL: '/api' });

// The find-care screen's routes, in application/serving/contract.py's shape: every plan, every
// service, and one service on one plan near one ZIP code.
export const getPlans = () => api.get('/plans');
export const getServices = () => api.get('/services');
export const getFindCare = ({ serviceId, planId, zip, radiusMi, sort }) => api.get('/find-care', {
  params: { service_id: serviceId, plan_id: planId, zip, ...(radiusMi ? { radius_mi: radiusMi } : {}), sort },
});

export default api;
