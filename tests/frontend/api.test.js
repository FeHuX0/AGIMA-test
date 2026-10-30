import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { api, setSession, ApiError } from "../../app/static/api.js";

beforeEach(() => setSession(null, () => {}));

test("includes CSRF token on mutation and never puts it in the URL", async t => {
  setSession("test-csrf");
  let sent;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    sent = { url, options };
    return Response.json({ id: "saved" }, { status: 202 });
  });
  assert.deepEqual(await api("/ask", { method: "POST", body: { question: "Вопрос" } }), { id: "saved" });
  assert.equal(sent.url, "/api/ask");
  assert.equal(sent.options.headers["X-CSRF-Token"], "test-csrf");
  assert.equal(sent.options.credentials, "same-origin");
});

test("expired session triggers login and retains server error", async t => {
  let expired = false;
  setSession("test", () => { expired = true; });
  t.mock.method(globalThis, "fetch", async () => Response.json({ error: { code: "unauthorized", message: "Войдите снова" } }, { status: 401 }));
  await assert.rejects(api("/conversations"), error => error instanceof ApiError && error.status === 401);
  assert.equal(expired, true);
});

test("database outage is an error rather than a fabricated answer", async t => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ error: { code: "database_unavailable", message: "Хранилище недоступно" } }, { status: 503 }));
  await assert.rejects(api("/ask", { method: "POST", body: {} }), /Хранилище недоступно/);
});

test("handles successful deletion without JSON", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 204 }));
  assert.equal(await api("/conversations", { method: "DELETE" }), null);
});
