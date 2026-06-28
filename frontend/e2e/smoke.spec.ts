import { expect, test } from '@playwright/test'

test('app renders in the browser', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('header')).toContainText('LoomSystem')
  await expect(page.getByRole('navigation')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Dashboard' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Projects' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Operator' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible()
})
