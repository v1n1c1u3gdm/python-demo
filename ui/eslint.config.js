import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'coverage/**', 'node_modules/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node }
    }
  },
  {
    files: ['**/*.{js,vue}'],
    rules: { complexity: ['error', 10] }
  },
  {
    files: ['tests/**/*.{js,vue}'],
    rules: { complexity: 'off' }
  },
  {
    files: ['tests/**/*.js'],
    languageOptions: {
      globals: globals.vitest
    }
  }
]
