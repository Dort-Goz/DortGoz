import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // ⚠ "localhost" DEĞİL, 127.0.0.1: Node 18+ localhost'u önce ::1'e (IPv6)
    // çözüyor, uvicorn ise 127.0.0.1'e (IPv4) bağlanıyor → proxy ECONNREFUSED
    // alıyor ve arayüz SESSİZCE boş görünüyor (video listesi boş, deney paneli
    // hiç açılmıyor, WS bağlanmıyor). Ölçüldü 2026-08-05.
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
