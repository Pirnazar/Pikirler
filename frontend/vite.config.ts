import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend sets session cookies for its own origin, so in development the
// API is proxied through Vite and the browser only ever talks to one origin.
const API_TARGET = process.env.PIKIRLER_API_URL ?? "http://127.0.0.1:8460";

// Sub-path the built interface is served under: "/" by default, "/pikirler/"
// in the production stack (docker-compose.prod.yml), where the server's root
// is shared with other apps. Only asset URLs change: routing is hash-based
// and the API keeps its own absolute prefix (/api/v1/pikirler).
function normaliseBase(raw: string | undefined): string {
  const trimmed = (raw ?? "").trim().replace(/^\/+|\/+$/g, "");
  return trimmed ? `/${trimmed}/` : "/";
}

export default defineConfig({
  base: normaliseBase(process.env.PIKIRLER_BASE_PATH),
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
