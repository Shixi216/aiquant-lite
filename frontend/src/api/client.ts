import axios from "axios";

export const api = axios.create({ baseURL: "/api", timeout: 30000 });
export const bff = axios.create({ baseURL: "/webui", timeout: 30000 });

export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string" && /[\u4e00-\u9fff]/.test(detail)) return detail;
    if (err.code === "ECONNABORTED" || err.code === "ETIMEDOUT") return "请求超时，请稍后重试";
    if (err.code === "ERR_NETWORK") return "无法连接数据服务，请检查本地服务是否已启动";
    const messages: Record<number, string> = {
      400: "请求参数有误，请检查后重试", 401: "身份验证未通过", 403: "当前请求没有访问权限",
      404: "暂无对应数据或研究产物", 422: "请求参数不符合要求", 429: "请求过于频繁，请稍后重试",
      500: "数据服务发生异常，请稍后重试", 503: "数据服务暂不可用，请稍后重试", 504: "数据服务响应超时，请稍后重试",
    };
    return messages[err.response?.status ?? 0] ?? "请求未完成，请稍后重试";
  }
  const message = err instanceof Error ? err.message : String(err);
  return /[\u4e00-\u9fff]/.test(message) ? message : "请求未完成，请稍后重试";
}

export function isRouterDown(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 503;
}
