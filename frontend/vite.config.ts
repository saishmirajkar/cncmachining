import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // The read-only preview (VITE_PREVIEW=1) is served from an arbitrary static path, so assets are relative.
  base: process.env.VITE_PREVIEW === "1" ? "./" : "/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (/node_modules\/(three|@react-three)\//.test(id)) return "three";
          if (/node_modules\/(@codemirror|@lezer|codemirror)\//.test(id)) return "codemirror";
          return undefined;
        },
      },
    },
  },
});
