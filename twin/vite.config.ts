/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";

// Cesium loads its workers, widgets and assets at runtime from CESIUM_BASE_URL. Production builds copy
// them to dist/cesium; the dev server serves them straight from node_modules (the copy plugin does not
// serve them in dev: they fell back to index.html, "Unexpected token '<'" in the twin, 2026-10-10).
const cesiumBuild = "node_modules/cesium/Build/Cesium";

export default defineConfig(({ command }) => ({
  define: { CESIUM_BASE_URL: JSON.stringify(command === "serve" ? `/${cesiumBuild}` : "/cesium") },
  plugins: [
    viteStaticCopy({
      targets: ["Workers", "ThirdParty", "Assets", "Widgets"].map((d) => ({ src: `${cesiumBuild}/${d}`, dest: "cesium" })),
    }),
  ],
  server: {
    port: 5174,
    strictPort: true,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true, ws: true } },
  },
  preview: { port: 5174 },
  build: { chunkSizeWarningLimit: 6000 },
  test: { environment: "node" },
}));
