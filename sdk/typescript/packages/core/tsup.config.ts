import { readFileSync } from "node:fs";
import { defineConfig } from "tsup";

// Single source of truth for the version: read it from package.json and inline
// it into the bundle via `define`, so `src/index.ts` never hardcodes a number.
const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string };

export default defineConfig({
  entry: ["src/index.ts", "src/capture/index.ts"],
  format: ["esm", "cjs"],
  clean: true,
  define: {
    __ADRIAN_CORE_VERSION__: JSON.stringify(version),
  },
});
