import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8000; in the demo FastAPI serves dist/.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/v1": "http://localhost:8000", "/health": "http://localhost:8000" } },
});
