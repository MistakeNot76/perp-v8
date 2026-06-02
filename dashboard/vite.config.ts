import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 9125,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:9125",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:9125",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 9125,
  },
});
