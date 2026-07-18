import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // The FastAPI backend serves ../static; CI builds this and the bundle
    // syncs it (gitignored, like the staged wheel).
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
