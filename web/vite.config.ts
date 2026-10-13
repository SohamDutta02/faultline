import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on vite and talks to `python -m api` on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
