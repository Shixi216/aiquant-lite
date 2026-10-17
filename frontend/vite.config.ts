import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8767", changeOrigin: false },
      "/webui": { target: "http://127.0.0.1:8767", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ["echarts/core", "echarts/charts", "echarts/components", "echarts/renderers"],
          vendor: ["vue", "vue-router", "pinia", "axios"],
        },
      },
    },
  },
});
