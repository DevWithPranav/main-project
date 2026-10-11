// Cesium loads its workers, widgets and assets at runtime from CESIUM_BASE_URL (vite.config.ts defines
// it: /node_modules/cesium/Build/Cesium in dev, /cesium in a build, where vite-plugin-static-copy puts
// them). Set it as a global too, before the first Cesium module runs, so the pre-bundled dependency sees
// it; the fallback covers a dev server still running with a config from before the define.
declare const CESIUM_BASE_URL: string;

const base = typeof CESIUM_BASE_URL !== 'undefined' ? CESIUM_BASE_URL : import.meta.env.DEV ? '/node_modules/cesium/Build/Cesium' : '/cesium';
(window as unknown as { CESIUM_BASE_URL: string }).CESIUM_BASE_URL = base;

export {};
