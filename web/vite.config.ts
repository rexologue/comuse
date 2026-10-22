/// <reference types="vitest/config" />
// The dev server proxies /api to main and adds the API token itself: the browser never
// holds it and needs no CORS. Where main is and where the token lies come from
// AGENT_API_URL / AGENT_TOKEN_FILE (the dev container) or, run by hand, from the
// runtime.json the launcher writes in ../data (AGENT_API_TOKEN when API_TOKEN is set).
import fs from "node:fs";
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const runtime = readJson(path.resolve(__dirname, "../data/runtime.json"));

const apiUrl = process.env.AGENT_API_URL ?? runtime?.main_url ?? "http://127.0.0.1:8000";
const tokenFile = process.env.AGENT_TOKEN_FILE ?? runtime?.token_file;

// Read on every request: main makes the token file on its first start, maybe after us.
function apiToken(): string | undefined {
  if (process.env.AGENT_API_TOKEN) return process.env.AGENT_API_TOKEN;
  if (!tokenFile) return undefined;

  try {
    return fs.readFileSync(tokenFile, "utf8").trim() || undefined;
  } catch {
    return undefined;
  }
}

function readJson(file: string): Record<string, string | undefined> | undefined {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return undefined;
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiUrl,
        // The browser's Host (localhost:5173) stays: main answers localhost and 127.0.0.1.
        changeOrigin: false,
        rewrite: (url) => url.replace(/^\/api/, ""),
        configure: (proxy) => {
          proxy.on("proxyReq", (request) => {
            const token = apiToken();
            if (token) request.setHeader("Authorization", `Bearer ${token}`);
          });
        },
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Libraries change less often than our code: their chunks stay cached.
        manualChunks: {
          react: ["react", "react-dom", "react-router"],
          data: ["@tanstack/react-query", "@microsoft/fetch-event-source"],
          markdown: ["react-markdown", "remark-gfm"],
          ui: ["@radix-ui/react-dropdown-menu", "@radix-ui/react-scroll-area", "@radix-ui/react-tooltip", "lucide-react"],
        },
      },
    },
  },
  test: {
    environment: "node",
  },
});
