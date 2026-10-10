/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '..');

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

export default defineConfig({
  plugins: [react(), repoScenes()],
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [repoRoot] },
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
  preview: { port: 5173 },
  build: { chunkSizeWarningLimit: 1500 },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
