// SPDX-License-Identifier: MIT
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  // Relative asset paths so the same build works behind nginx and on a static host (demo mode).
  base: "./",
  plugins: [react()],
  server: {
    proxy: {
      "/api": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
