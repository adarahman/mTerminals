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
  const strike = 24_600;
  const chainRow = {
    strike,
    footprintScore: 72,
    footprintFactors: { capitalActivity: 90, oiChangeActivity: 80, turnoverActivity: 70 },
    ceOI: 1_200_000, ceChgOI: 80_000, ceVol: 2_400_000, ceLTP: 120,
    ceSignal: 'Short build', ceCapitalFlow: 9_600_000,
    peOI: 1_000_000, peChgOI: 60_000, peVol: 1_800_000, peLTP: 105,
    peSignal: 'Long build', peCapitalFlow: 6_300_000,
  };
  await page.evaluate(({ row }) => {
    const fixture = {
      symbol: 'NIFTY', expiry: '31-DEC-2099', spot: row.strike,
      atm: row.strike, chain: [row], greeks: [], oiVelocity: [],
      lastUpdated: new Date().toISOString(),
    };
    window.parseAndRender(JSON.stringify(fixture));
  }, { row: chainRow });

  const popupPromise = page.waitForEvent('popup');
  await page.evaluate((selectedStrike) => window.openOptionChainAtStrike(selectedStrike), strike);
  const optionChain = await popupPromise;
  await optionChain.waitForLoadState('domcontentloaded');

  await page.evaluate(({ row }) => {
    const chan = new BroadcastChannel('oc-live-sync');
    chan.postMessage({
      rows: [{
        strike: row.strike, isAtm: true, pcr: '0.83', pcrChg: '+0.02',
        ce: { oi: row.ceOI, oiChg: row.ceChgOI, vol: row.ceVol, ltp: row.ceLTP, signal: row.ceSignal },
        pe: { oi: row.peOI, oiChg: row.peChgOI, vol: row.peVol, ltp: row.peLTP, signal: row.peSignal },
      }],
      symbol: 'NIFTY', spot: row.strike, expiry: '31-DEC-2099',
      expiryDates: ['31-DEC-2099'], range: 10,
      feedState: { status: 'LIVE', quality: 'FULL', marketSession: 'LIVE' },
    });
    chan.close();
  }, { row: chainRow });

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
