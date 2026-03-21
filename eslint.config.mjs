import tsParser from "@typescript-eslint/parser";
import security from "eslint-plugin-security";

export default [
  {
    ignores: [
      "node_modules/**",
      "dist/**",
      "dist-electron/**",
      "release/**",
      "backend/**",
    ],
  },
  {
    files: ["src/**/*.{ts,tsx}", "electron/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
    plugins: {
      security,
    },
    rules: {
      // High-signal security rules for TS/Electron code.
      "security/detect-child-process": "error",
      "security/detect-disable-mustache-escape": "error",
      "security/detect-eval-with-expression": "error",
      "security/detect-new-buffer": "error",
      "security/detect-non-literal-require": "error",
      "security/detect-unsafe-regex": "error",

      // Noisy in typed codebases with validated/path-resolved variables.
      "security/detect-non-literal-fs-filename": "off",
      "security/detect-non-literal-regexp": "off",
      "security/detect-object-injection": "off",
    },
  },
];
