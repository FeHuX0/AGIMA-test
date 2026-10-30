import { api } from "./api.js";

// Network-only repository; no browser persistence or locally generated AI answers.
export const conversations = {
  list: (offset = 0) => api(`/conversations?limit=5&offset=${offset}`),
  history: (before, signal) => api(`/history?limit=5${before ? `&before=${encodeURIComponent(before)}` : ""}`, { signal }),
  get: (id, { before, through, signal } = {}) => {
    const query = new URLSearchParams();
    if (before) query.set("before", before);
    if (through) query.set("through", through);
    return api(`/conversations/${encodeURIComponent(id)}${query.size ? `?${query}` : ""}`, { signal });
  },
  removeAll: () => api("/conversations", { method: "DELETE" }),
  remove: (id) => api(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  submit: (body) => api("/ask", { method: "POST", body }),
  request: (id, signal) => api(`/requests/${encodeURIComponent(id)}`, { signal }),
  workerAvailable: async (signal) => {
    try { await api("/ready", { signal }); return true; }
    catch (error) { if (error.status === 503) return false; throw error; }
  },
};
