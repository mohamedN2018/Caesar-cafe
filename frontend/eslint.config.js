/**
 * ESLint, flat config.
 *
 * `npm run lint` has been in package.json since Phase 1 and has never run: the
 * script was written for ESLint 8's `.eslintrc`, ESLint 9 wants this file, and
 * nothing failed loudly enough for anybody to notice. A lint script that exits
 * non-zero for the wrong reason is worse than none — it trains you to ignore it.
 *
 * The rule set is deliberately narrow. `vue-tsc` already runs on every build and
 * catches the type errors, so duplicating it here would only produce two voices
 * disagreeing about the same line. What is left is the class of mistake a type
 * checker cannot see: an unused import that survives a refactor, a `v-for`
 * without a key, a mutated prop.
 */
import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'src/types/api.d.ts'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.serviceworker },
      parserOptions: {
        // The <script> block inside a .vue file is TypeScript; without this the
        // Vue parser hands it to the default parser and every type annotation
        // is a syntax error.
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    rules: {
      // `_unused` is how this codebase marks a parameter it must accept and
      // does not use — a signature it does not control.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // Multi-word component names are a Vue convention aimed at avoiding
      // collisions with HTML elements. Every component here lives behind an
      // explicit import, so there is nothing to collide with.
      'vue/multi-word-component-names': 'off',
      // Formatting is Prettier's job in the editor; an ESLint opinion about
      // line breaks only produces noise in CI.
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/attributes-order': 'off',
    },
  },
  {
    // The service worker runs outside the bundle and outside TypeScript.
    files: ['public/sw.js'],
    languageOptions: {
      globals: { ...globals.serviceworker },
    },
  },
]
