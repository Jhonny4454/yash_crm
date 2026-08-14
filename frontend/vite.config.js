import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Flask serves the local build below /app, while Render's static service
// serves it from /. Keeping that distinction in one environment setting
// avoids production HTML pointing at /app/assets/* files that do not exist on
// the static host.
const basePath = process.env.VITE_BASE_PATH || "/app/";

export default defineConfig({
  base: basePath,
  plugins: [react()],
  server: {
    port: 5173,
    // Lets you call the API as /api/v1/... in dev without CORS issues.
    proxy: {
      "/api": {
        target: "http://localhost:5000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
