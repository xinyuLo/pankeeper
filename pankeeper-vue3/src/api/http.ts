import axios from 'axios';
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
export const http = axios.create({
    baseURL: import.meta.env.VITE_API_BASE || '/api',
    timeout: 35000,
});
http.interceptors.request.use((cfg) => {
    const token = localStorage.getItem('pk-auth');
    if (token)
        cfg.headers.Authorization = `Bearer ${token}`;
    return cfg;
});
http.interceptors.response.use((res) => res.data, (err) => {
    if (err?.response?.status === 401) {
        localStorage.removeItem('pk-auth');
        if (location.pathname !== '/login')
            window.location.assign('/login');
    }
    return Promise.reject(err);
});
export function mockDelay<T>(data: T, ms = 120): Promise<T> {
    return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}
export async function get<T>(url: string, config?: Record<string, unknown>): Promise<T> {
    return (await http.get(url, config)) as unknown as T;
}
export async function post<T>(url: string, body?: unknown, config?: Record<string, unknown>): Promise<T> {
    return (await http.post(url, body, config)) as unknown as T;
}
export async function put<T>(url: string, body?: unknown, config?: Record<string, unknown>): Promise<T> {
    return (await http.put(url, body, config)) as unknown as T;
}
export async function del<T>(url: string, config?: Record<string, unknown>): Promise<T> {
    return (await http.delete(url, config)) as unknown as T;
}
