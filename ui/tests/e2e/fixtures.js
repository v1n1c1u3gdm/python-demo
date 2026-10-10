import { rmSync } from 'node:fs'
import { test as base, chromium } from '@playwright/test'
import { certificateSpkiPin } from '../../playwright.config.js'

export const test = base.extend({
  // Playwright requires fixture callbacks to destructure their dependencies, even when none are consumed.
  // eslint-disable-next-line no-empty-pattern
  context: async ({ }, use, testInfo) => {
    const profile = testInfo.outputPath('profile')
    const context = await chromium.launchPersistentContext(profile, {
      headless: true,
      acceptDownloads: false,
      args: [`--ignore-certificate-errors-spki-list=${certificateSpkiPin}`]
    })
    try {
      await use(context)
    } finally {
      await context.close()
      rmSync(profile, { recursive: true, force: true })
    }
  },
  page: async ({ context }, use) => {
    const page = context.pages()[0] || await context.newPage()
    await use(page)
  }
})
