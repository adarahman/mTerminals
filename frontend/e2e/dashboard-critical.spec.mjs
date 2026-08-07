import { test, expect } from '@playwright/test';

async function openDashboard(page) {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto('');
  await expect(page.locator('#sec-nav-bar')).toBeVisible();
  return pageErrors;
}

test('dashboard loads and exposes primary tools without page errors', async ({ page }) => {
  const pageErrors = await openDashboard(page);
  await expect(page.locator('#oi-flow-open-btn')).toBeVisible();
  await expect(page.locator('#backtest-toggle-btn')).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test('OI Flow modal opens, traps focus, and restores its invoker', async ({ page }) => {
  await openDashboard(page);
  const trigger = page.locator('#oi-flow-open-btn');
  await trigger.click();

  const modal = page.locator('#oi-flow-modal');
  await expect(modal).toHaveClass(/open/);
  await expect(modal).toHaveAttribute('role', 'dialog');
  await expect(modal).toHaveAttribute('aria-modal', 'true');

  await page.keyboard.press('Escape');
  await expect(modal).not.toHaveClass(/open/);
  await expect(trigger).toBeFocused();
});

test('Backtest modal uses the shared accessible modal contract', async ({ page }) => {
  await openDashboard(page);
  const trigger = page.locator('#backtest-toggle-btn');
  await trigger.click();

  const modal = page.locator('#backtest-modal');
  await expect(modal).toHaveClass(/open/);
  await expect(modal).toHaveAttribute('role', 'dialog');
  await expect(modal).toHaveAttribute('aria-modal', 'true');
  await expect(page.getByRole('button', { name: 'Close backtest' })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(modal).not.toHaveClass(/open/);
  await expect(trigger).toBeFocused();
});

test('Option Chain hands Strike Detail back to the Dashboard', async ({ page }) => {
  await openDashboard(page);
  const popupPromise = page.waitForEvent('popup');
  await page.evaluate(() => window.openOptionChain());
  const optionChain = await popupPromise;
  await optionChain.waitForLoadState('domcontentloaded');

  const firstRow = optionChain.locator('#ocBody .oc-row').first();
  await expect(firstRow).toBeVisible({ timeout: 20_000 });
  await firstRow.click();
  await optionChain.getByRole('button', { name: /Open Strike Detail/ }).click();

  await expect(optionChain).toBeClosed();
  await expect(page.locator('#strike-detail-report-modal')).toHaveClass(/open/);
  await expect(page.locator('#strike-detail-report-content .sdr-hero')).toBeVisible();
});

test('compact dashboard keeps primary navigation within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await openDashboard(page);
  await expect(page.locator('#sec-nav-bar')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});
