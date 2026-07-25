import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

const dependency = (name: string) =>
  fileURLToPath(new URL(`./node_modules/${name}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      react: dependency("react"),
      "react-dom": dependency("react-dom"),
      "react-router-dom": dependency("react-router-dom"),
      "react-hot-toast": dependency("react-hot-toast"),
      "react-markdown": dependency("react-markdown"),
      "remark-gfm": dependency("remark-gfm"),
      zustand: dependency("zustand"),
      axios: dependency("axios"),
      clsx: dependency("clsx"),
      "file-saver": dependency("file-saver"),
      "framer-motion": dependency("framer-motion"),
      "@headlessui/react": dependency("@headlessui/react"),
      "@heroicons/react": dependency("@heroicons/react"),
      "@dnd-kit/core": dependency("@dnd-kit/core"),
      "@dnd-kit/modifiers": dependency("@dnd-kit/modifiers"),
      "@dnd-kit/sortable": dependency("@dnd-kit/sortable"),
      "@dnd-kit/utilities": dependency("@dnd-kit/utilities"),
    },
  },
  build: {
    outDir: "../../../apps/platform-console/static/lakemeter",
    emptyOutDir: true,
    lib: {
      entry: "src/entry.tsx",
      formats: ["es"],
      fileName: () => "entry.js",
    },
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        assetFileNames: (asset) =>
          asset.name?.endsWith(".css") ? "style.css" : "assets/[name]-[hash][extname]",
      },
    },
  },
});
