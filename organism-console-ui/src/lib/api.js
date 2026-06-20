import axios from 'axios';
const API_BASE_URL = 'http://127.0.0.1:8000';
export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});
export const backendHealth = async () => {
  try {
    const response = await api.get('/health');
    return { ...response.data, healthy: response.data.status === 'ok' };
  } catch (error) {
    return { error: 'Backend not reachable', healthy: false };
  }
};
export const getTraces = async (limit = 50) => {
  try {
    const response = await api.get(`/traces?limit=${limit}`);
    return response.data;
  } catch (error) {
    return [];
  }
};
