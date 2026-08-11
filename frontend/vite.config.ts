import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        application: "index.html",
        administrator: "admin.html",
        swagger: "swagger.html",
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://web:8000",
    },
  },
  test: {
    environment: "jsdom",
    maxWorkers: 1,
    pool: "threads",
    setupFiles: "./src/test/setup.ts",
  },
});
