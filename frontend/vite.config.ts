/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { viteStaticCopy } from 'vite-plugin-static-copy';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '..');

// The 3D twin page (/twin) runs Cesium, which loads its workers, widgets and assets at runtime from
// CESIUM_BASE_URL: production builds copy them to dist/cesium; the dev server serves them straight
// from node_modules (the copy plugin does not serve them in dev).
const cesiumBuild = 'node_modules/cesium/Build/Cesium';

/**
 * Dev-only: serves the repo's lane maps to the mock API (VITE_MOCK=1), so mock mode draws the
 * real CARLA towns without copying multi-MB JSON into the bundle. Read-only, whitelisted names.
 */
function repoScenes(): Plugin {
  return {
    name: 'repo-scenes',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__repo/scenes', async (req, res) => {
        const name = (req.url ?? '').replace(/^\//, '').split('?')[0];
        if (!/^Town\d+[A-Za-z_]*\.json$/.test(name)) {
          res.statusCode = 404;
          res.end('not found');
          return;
        }
        try {
          const body = await readFile(path.join(repoRoot, 'ml/violation_engine/configs/scenes', name));
          res.setHeader('Content-Type', 'application/json');
          res.end(body);
        } catch {
          res.statusCode = 404;
          res.end('not found');
        }
      });
    },
  };
}

export default defineConfig(({ command }) => ({
  define: { CESIUM_BASE_URL: JSON.stringify(command === 'serve' ? `/${cesiumBuild}` : '/cesium') },
  plugins: [
    react(),
    repoScenes(),
    viteStaticCopy({
      // stripBase 4: node_modules/cesium/Build/Cesium/Workers/x.js -> dist/cesium/Workers/x.js
      targets: ['Workers', 'ThirdParty', 'Assets', 'Widgets'].map((d) => ({ src: `${cesiumBuild}/${d}`, dest: 'cesium', rename: { stripBase: 4 } })),
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [repoRoot] },
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
  preview: { port: 5173 },
  build: { chunkSizeWarningLimit: 6000 }, // the lazy-loaded twin chunk carries Cesium
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
}));
