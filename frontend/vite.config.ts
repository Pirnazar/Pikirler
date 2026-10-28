import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend sets session cookies for its own origin, so in development the
// API is proxied through Vite and the browser only ever talks to one origin.
const API_TARGET = process.env.PIKIRLER_API_URL ?? "http://127.0.0.1:8460";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5480,
    strictPort: true,
    proxy: { "/api": { target: API_TARGET, changeOrigin: false } },
  },
  preview: {
    host: "127.0.0.1",
    port: 5481,
    proxy: { "/api": { target: API_TARGET, changeOrigin: false } },
  },
  build: { target: "es2022", sourcemap: false },
});
