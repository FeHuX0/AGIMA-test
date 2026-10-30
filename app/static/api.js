let csrfToken = null;
let onUnauthorized = () => {};
let sessionVersion = 0;

export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export function setSession(token, handler) {
  sessionVersion++;
  csrfToken = token;
  if (handler) onUnauthorized = handler;
}

export async function api(path, { method = "GET", body, signal } = {}) {
  const startedSession = sessionVersion;
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("Нет связи с сервером. Проверьте соединение и повторите действие.", "network", 0);
  }
  if (response.status === 204) return null;
  let data;
  try { data = await response.json(); }
  catch { throw new ApiError("Сервер вернул некорректный ответ.", "invalid_response", response.status); }
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login" && startedSession === sessionVersion) onUnauthorized();
    throw new ApiError(data.error?.message || "Не удалось выполнить действие.", data.error?.code, response.status);
  }
  return data;
}
