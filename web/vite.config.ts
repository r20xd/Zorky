import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Бэкенд за прокси: фронт ходит на /api своего же адреса, CORS не нужен.
// 127.0.0.1, а не localhost: Node может резолвить localhost в ::1, а бэкенд слушает IPv4.
// changeOrigin выключен: бэкенд сверяет Origin браузера с Host (защита от CSRF), с подменённым
// Host вход и любое изменение статуса при включённой авторизации давали 403 «чужой Origin».
const proxy = {
  "/api": {
    target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8080",
    changeOrigin: false,
  },
};

// `py tasks.py run --no-docker`: vite preview по HTTPS с сертификатом `py tasks.py cert`
// (MOSPROM_CERT/MOSPROM_KEY), доступен телефону по IP ноутбука. Заголовков безопасности nginx тут нет.
const cert = process.env.MOSPROM_CERT;
const key = process.env.MOSPROM_KEY;

export default defineConfig({
  plugins: [react()],
  // 127.0.0.1: так до сервера доходит и телефон по USB (adb reverse ходит по IPv4)
  server: { port: 5173, host: "127.0.0.1", proxy },
  preview: {
    host: "0.0.0.0",
    port: 8443,
    strictPort: true,
    allowedHosts: true,
    https: cert && key ? { cert: readFileSync(cert), key: readFileSync(key) } : undefined,
    proxy,
  },
});
