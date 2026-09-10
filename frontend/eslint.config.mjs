import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = defineConfig([
  ...nextVitals,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored third-party app (see vendor/system-design-lab/VENDOR.md).
    "vendor/**",
  ]),
]);

export default eslintConfig;
