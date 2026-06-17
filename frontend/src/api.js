import axios from 'axios';

// Point this to your FastAPI server
const API = axios.create({
    baseURL: 'http://127.0.0.1:8000/api',
});

export const fetchCountries = async () => {
    const response = await API.get('/countries');
    return response.data;
};

export const fetchHistory = async () => {
    const response = await API.get('/history');
    return response.data;
};

export const analyzeLead = async (leadData) => {
    const { web_rate, api_rate, net_rate, min_value, ent_mult, ...rest } = leadData;
    const payload = {
        ...rest,
        pricing_rules: { web: web_rate, api: api_rate, net: net_rate, min: min_value, ent: ent_mult }
    };
    const response = await API.post('/process', payload);
    return response.data;
};

export const analyzeLeadBatch = async (batchData) => {
    const response = await API.post('/process-batch', batchData);
    return response.data;
};
export const sendLogToServer = async (level, message) => {
    try {
        await API.post('/log', { level, message });
    } catch (e) {
        console.error("Failed to stream log to backend:", e);
    }
};