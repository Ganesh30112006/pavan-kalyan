import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'coverage'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.node } },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/no-explicit-any': 'error',
      'no-console': 'error',
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'Render untrusted text as text. dangerouslySetInnerHTML is not allowed.',
        },
        {
          selector: "MemberExpression[object.name='localStorage']",
          message: 'Browser storage is not used; never store tokens, credentials or API data in it.',
        },
        {
          selector: "MemberExpression[object.name='sessionStorage']",
          message: 'Session storage is not used; never store tokens or secrets in the browser.',
        },
      ],
    },
  },
  { files: ['tests/**'], rules: { 'react-refresh/only-export-components': 'off' } },
);
