import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

const apiProxyTarget = process.env.DORTGOZ_API_PROXY_TARGET ?? "http://127.0.0.1:8000";
const apiProxy = {
  "/api": apiProxyTarget,
  "/health": apiProxyTarget,
  "/media": apiProxyTarget,
  "/ws": { target: apiProxyTarget.replace(/^http/, "ws"), ws: true },
};

const allowedHosts = (process.env.DORTGOZ_ALLOWED_HOSTS ?? "")
  .split(",")
  .map((entry) => entry.trim())
  .filter(Boolean);

const server = {
  proxy: apiProxy,
  ...(allowedHosts.length > 0 ? { allowedHosts } : {}),
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server,
  preview: server,
});
