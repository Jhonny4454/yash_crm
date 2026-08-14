import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Flask serves the local build below /app, while Render's static service
// serves it from /. Keeping that distinction in one environment setting
// avoids production HTML pointing at /app/assets/* files that do not exist on
// the static host.
const basePath = process.env.VITE_BASE_PATH || "/app/";

/**
 * Open the connection to the API before any JavaScript asks for it.
 *
 * On Render the SPA and the API are two different hosts, so the very first
 * API call - `/auth/staff/me`, fired the moment React mounts - has to do a DNS
 * lookup, a TCP handshake and a TLS handshake before it can send a single
 * byte. That is three round trips to Singapore stacked in front of the first
 * thing the operator sees after signing in.
 *
 * A <link rel="preconnect"> in the HTML starts all three while the bundle is
 * still downloading, so by the time the call is made the connection is warm.
 * Injected from a plugin rather than written into index.html, because the API
 * origin is only known at build time and a hard-coded `%VITE_API_URL%` that
 * nobody set would ship a broken tag.
 */
function preconnectToApi() {
  return {
    name: "preconnect-to-api",
    transformIndexHtml(html) {
      const raw = process.env.VITE_API_URL;
      if (!raw) return html;               // same-origin build; nothing to warm
      let origin;
      try {
        origin = new URL(raw).origin;
      } catch {
        return html;                       // relative base, so same origin
      }
      return {
        html,
        tags: [
          { tag: "link", attrs: { rel: "preconnect", href: origin, crossorigin: "" },
            injectTo: "head-prepend" },
          { tag: "link", attrs: { rel: "dns-prefetch", href: origin },
            injectTo: "head-prepend" },
        ],
      };
    },
  };
}

export default defineConfig({
  base: basePath,
  plugins: [react(), preconnectToApi()],
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
    rollupOptions: {
      output: {
        /* Split the parts that never change away from the parts that do.
         *
         * Everything used to land in one entry chunk, so shipping a one-line
         * fix to a screen changed the hash on React, React Router and axios
         * too, and every operator re-downloaded the whole ~250 KB on the next
         * visit. Vendor code changes when a dependency is upgraded - a few
         * times a year - and stays in the browser cache in between.
         */
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("react-router")) return "vendor-router";
          if (id.includes("/react/") || id.includes("/react-dom/")
              || id.includes("/scheduler/")) return "vendor-react";
          if (id.includes("axios")) return "vendor-http";
          return "vendor";
        },
      },
    },
  },
});
