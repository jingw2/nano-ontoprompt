/**
 * auth.spec.ts — login/logout/registration flows. Self-skips until the
 * auth API is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

test.describe('Authentication', () => {
  test('login page renders', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await page.goto('/login')
    await expect(page.locator('h1')).toContainText('Ontexus')
    await expect(page.locator('button[type="submit"]')).toBeVisible()
  })

  test('redirects unauthenticated users to login', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await page.goto('/overview')
    await expect(page).toHaveURL(/\/login/)
  })

  test('login with valid credentials', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await loginAsAdmin(page)
    await expect(page).toHaveURL(/\/overview$/)
  })

  test('login with wrong password shows error', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await page.goto('/login')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'wrongpassword')
    await page.click('button[type="submit"]')
    await expect(page.locator('text=用户名或密码错误')).toBeVisible()
  })

  test('register page accessible', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await page.goto('/register')
    await expect(page.locator('h1')).toContainText('注册')
  })

  test('logout redirects to login', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await loginAsAdmin(page)
    await page.click('button:has-text("退出")')
    await expect(page).toHaveURL(/\/login/)
  })

  test('language toggle works', async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/auth')), 'backend /api/v1/auth not registered yet')
    await loginAsAdmin(page)
    await page.click('button:has-text("EN")')
    await expect(page.locator('h2:has-text("Overview")')).toBeVisible()
    await page.click('button:has-text("中")')
    await expect(page.locator('h2:has-text("概览")')).toBeVisible()
  })
})
