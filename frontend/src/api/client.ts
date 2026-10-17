import axios from "axios";

export const api = axios.create({ baseURL: "/api", timeout: 30000 });
export const bff = axios.create({ baseURL: "/webui", timeout: 30000 });

export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return err.message;
  }
  return String(err);
}

export function isRouterDown(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 503;
}
