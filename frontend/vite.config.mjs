import { defineConfig } from "vite";

export default defineConfig({
  build: {
    assetsInlineLimit: 0,
    emptyOutDir: true,
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
    rollupOptions: {
      output: {
        assetFileNames: "assets/[name]-[hash][extname]",
        chunkFileNames: "assets/[name]-[hash].js",
        entryFileNames: "assets/app-[hash].js"
      }
    }
  }
});
