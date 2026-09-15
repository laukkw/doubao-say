import { defineConfig } from '@rstest/core';

export default defineConfig({
  include: ['e2e/**/*.test.ts'],
  testTimeout: 8 * 60 * 1000,
  hookTimeout: 2 * 60 * 1000,
  pool: {
    maxWorkers: 1,
  },
});
