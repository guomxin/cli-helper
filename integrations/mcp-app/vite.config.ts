import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  base: "./",
  plugins: [viteSingleFile()],
  build: {
    target: "es2022",
    outDir: "../../bscli/mcp/static",
    emptyOutDir: false,
    rollupOptions: {
      input: "trusted-interaction.html",
    },
  },
});
