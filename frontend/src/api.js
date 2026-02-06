import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
    baseURL: API_BASE_URL,
});

export const getHospitals = () => api.get('/hospitals');
export const getRates = (code, hospital) => {
    const params = {};
    if (code) params.code = code;
    if (hospital) params.hospital = hospital;
    return api.get('/rates', { params });
};

export default api;
