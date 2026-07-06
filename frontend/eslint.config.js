import pluginVue from 'eslint-plugin-vue'
import js from '@eslint/js'

export default [
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['src/**/*.{js,vue}'],
    rules: {
      // ── Unused variables / imports ──────────────────────────────────
      'no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],

      // ── Common quality ──────────────────────────────────────────────
      'no-console': 'off',          // keep console.log in trading app
      'no-debugger': 'warn',
      'no-undef': 'off',            // Vue compiler macros (defineProps etc.)
      'no-constant-condition': 'warn',
      'no-empty': ['warn', { allowEmptyCatch: true }],

      // ── Vue-specific ────────────────────────────────────────────────
      'vue/multi-word-component-names': 'off',     // TopBar.vue, Home.vue
      'vue/no-unused-vars': 'warn',
      'vue/no-unused-components': 'warn',
      'vue/require-default-prop': 'off',
      'vue/require-prop-types': 'off',
      'vue/html-self-closing': 'off',              // style preference
      'vue/max-attributes-per-line': 'off',        // style preference
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-indent': 'off',                    // Vite/Prettier handles this
      'vue/html-closing-bracket-newline': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/attribute-hyphenation': 'off',
      'vue/attributes-order': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-closing-bracket-spacing': 'off',
      'vue/v-on-event-hyphenation': 'off',
      'vue/order-in-components': 'off',
    },
  },
  {
    ignores: ['dist/', 'node_modules/'],
  },
]
