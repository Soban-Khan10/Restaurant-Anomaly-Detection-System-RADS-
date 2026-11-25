/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The app server's CORS settings allow exactly this port, so never silently move to another one.
    strictPort: true,
  },
  build: {
    rollupOptions: {
      output: {
        // Libraries in their own files: smaller app bundle, and the browser keeps them cached between updates.
        manualChunks: {
          react: ["react", "react-dom", "react-router"],
          charts: ["recharts"],
        },
      },
    },
  },
  test: {
    environment: "node",
  },
});
