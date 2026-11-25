import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  { ignores: [".next/**", "node_modules/**", "test-results/**", "playwright-report/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript", "plugin:jsx-a11y/recommended"),
  {
    rules: {
      "jsx-a11y/no-redundant-roles": ["error", { ul: ["list"] }],
      "jsx-a11y/label-has-associated-control": ["error", { assert: "either", depth: 3 }],
    },
  },
];

export default config;
