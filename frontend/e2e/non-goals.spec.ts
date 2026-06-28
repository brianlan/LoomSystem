import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'

const BASE = '/api/v1'

async function mockAggregateStatus(page: Page) {
  await page.route(`${BASE}/status`, (route) =>
    route.fulfill({
      status: 200,
      body: JSON.stringify({
        running_reviewers: 0,
        running_implementors: 0,
        backlog_size: 0,
        recent_failures: [],
      }),
    }),
  )
}

async function mockEmptySettings(page: Page) {
  for (const path of [
    '/settings/agent-definitions',
    '/settings/model-entries',
    '/settings/docker-images',
  ]) {
    await page.route(`${BASE}${path}`, (route) =>
      route.fulfill({ status: 200, body: JSON.stringify([]) }),
    )
  }
  for (const path of ['/settings/ssh-key', '/settings/github-token', '/settings/triage-config']) {
    await page.route(`${BASE}${path}`, (route) =>
      route.fulfill({ status: 404, body: JSON.stringify({ detail: 'not set' }) }),
    )
  }
  await page.route(`${BASE}/settings/proxy`, (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ http_proxy: null, https_proxy: null }) }),
  )
}

async function mockEmptyProjects(page: Page) {
  await page.route(`${BASE}/projects`, (route) =>
    route.fulfill({ status: 200, body: JSON.stringify([]) }),
  )
}

async function mockEmptyNotifications(page: Page) {
  await page.route(`${BASE}/notifications?unread_only=false`, (route) =>
    route.fulfill({ status: 200, body: JSON.stringify([]) }),
  )
}

test.beforeEach(async ({ page }) => {
  await mockAggregateStatus(page)
  await mockEmptySettings(page)
  await mockEmptyProjects(page)
  await mockEmptyNotifications(page)
})

test('non-goal notices are visible on the dashboard', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.notices')).toContainText('no app-level authentication')
  await expect(page.locator('.notices')).toContainText('No resource caps')
  await expect(page.locator('.notices')).toContainText('Desktop only')
  await expect(page.locator('.notices')).toContainText('no external API/SDK')
})

test('no app-level authentication controls appear on any tab', async ({ page }) => {
  await page.goto('/')
  const authLabels = [/Login/i, /Sign in/i, /Sign out/i, /Password/i, /Authenticate/i, /Account/i]
  for (const tab of ['Dashboard', 'Projects', 'Operator', 'Settings']) {
    await page.getByRole('button', { name: tab }).click()
    for (const label of authLabels) {
      await expect(page.getByRole('button', { name: label })).toHaveCount(0)
      await expect(page.getByRole('link', { name: label })).toHaveCount(0)
      await expect(page.getByRole('textbox', { name: label })).toHaveCount(0)
    }
  }
})

test('no webhook or multi-host orchestration controls appear', async ({ page }) => {
  await page.goto('/')
  const labels = [/Webhook/i, /Cluster/i, /Node/i, /Host/i]
  for (const tab of ['Dashboard', 'Projects', 'Operator', 'Settings']) {
    await page.getByRole('button', { name: tab }).click()
    for (const label of labels) {
      await expect(page.getByRole('button', { name: label })).toHaveCount(0)
      await expect(page.getByRole('link', { name: label })).toHaveCount(0)
      await expect(page.getByRole('textbox', { name: label })).toHaveCount(0)
    }
  }
})

test('no resource cap or quota controls appear', async ({ page }) => {
  await page.goto('/')
  const labels = [/CPU/i, /Memory/i, /Quota/i, /Resource cap/i]
  for (const tab of ['Dashboard', 'Projects', 'Operator', 'Settings']) {
    await page.getByRole('button', { name: tab }).click()
    for (const label of labels) {
      await expect(page.getByRole('button', { name: label })).toHaveCount(0)
      await expect(page.getByRole('link', { name: label })).toHaveCount(0)
      await expect(page.getByRole('textbox', { name: label })).toHaveCount(0)
    }
  }
})

test('no cloud, SDK, or mobile product controls appear', async ({ page }) => {
  await page.goto('/')
  const labels = [/Cloud/i, /SDK/i, /Deploy/i, /Integration/i, /Mobile/i]
  for (const tab of ['Dashboard', 'Projects', 'Operator', 'Settings']) {
    await page.getByRole('button', { name: tab }).click()
    for (const label of labels) {
      await expect(page.getByRole('button', { name: label })).toHaveCount(0)
      await expect(page.getByRole('link', { name: label })).toHaveCount(0)
      await expect(page.getByRole('textbox', { name: label })).toHaveCount(0)
    }
  }
})
