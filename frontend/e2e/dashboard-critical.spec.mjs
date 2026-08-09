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
  await expect(page.locator('#rail-context-toggle')).toBeVisible();
  await expect(page.getByRole('button', { name: /Decision/ })).toBeVisible();
  await expect(page.locator('#rail-tools-toggle')).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test('native OI Flow chart opens and switches measure', async ({ page }) => {
  await openDashboard(page);
  await page.getByRole('button', { name:'Open OI Flow chart' }).first().click();
  const modal = page.locator('#oi-flow-modal');
  await expect(modal).toHaveClass(/open/);
  await expect(page.locator('#oi-flow-native-content')).toContainText('Combined OI + ΔOI');
  await expect(page.locator('#oi-flow-native-content .oi-combined-change').first()).toBeVisible();
  await page.getByRole('button', { name:'OI Bar Chart', exact:true }).click();
  await expect(page.locator('#oi-native-bar-canvas')).toBeVisible();
  await page.getByRole('button', { name:'Butterfly', exact:true }).click();
  await page.getByRole('button', { name:'OI Chg', exact:true }).click();
  await expect(page.locator('#oi-native-velocity-tabs')).toBeVisible();
  await page.locator('#oi-native-velocity-tabs').getByRole('button', { name:'5m', exact:true }).click();
  await expect(page.locator('#oi-flow-native-content')).toContainText('OI Change Velocity (5m)');
  await page.getByRole('button', { name:'Combined', exact:true }).click();
  await expect(page.locator('#oi-flow-native-content')).toContainText('Combined OI + ΔOI');
  await expect(page.locator('#oi-flow-native-content .oi-combined-change').first()).toBeVisible();
  await expect(page.locator('#oi-flow-native-content .oi-increase-segment').first()).toBeVisible();
  await expect(page.locator('#oi-flow-native-content .oi-decrease-segment').first()).toBeVisible();
  await page.getByRole('button', { name:'Close OI Flow chart' }).click();
  await expect(modal).not.toHaveClass(/open/);
});

test('Backtest modal uses the shared accessible modal contract', async ({ page }) => {
  await openDashboard(page);
  await page.locator('#rail-tools-toggle').click();
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

test('Option Chain strike opens dashboard-native Strike Detail', async ({ page }) => {
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

  await page.evaluate((selectedStrike) => window.openOptionChainAtStrike(selectedStrike), strike);
  await expect(page.locator('#strike-detail-report-modal')).toHaveClass(/open/);
  await expect(page.locator('#strike-detail-report-content .sdr-hero')).toBeVisible();
});

test('Option Chain Snapshot header opens and closes the native chain table', async ({ page }) => {
  await openDashboard(page);
  const header = page.locator('#chain-summary-card .section-header');
  const table = page.locator('#option-chain-table');
  await expect(header).toHaveAttribute('aria-expanded', 'false');
  await expect(table).toBeHidden();
  await header.click();
  await expect(header).toHaveAttribute('aria-expanded', 'true');
  await expect(table).toBeVisible();
  await expect(table.getByRole('columnheader', { name: /CE LTP/ })).toBeVisible();
  await expect(table.getByRole('columnheader', { name: /PE LTP/ })).toBeVisible();
  await expect(table.getByRole('columnheader', { name: /Footprint/ })).toBeVisible();
  await table.locator('.oc-ledger-tools').getByRole('button', { name: /Greeks/ }).click();
  await expect(table.locator('.oc-ledger-greeks').first()).toBeVisible();
  await table.locator('.oc-ledger-row td.ltp.ce button').first().click();
  await expect(page.locator('#pt-quick-popover')).toBeVisible();
  await page.locator('#pt-quick-popover .pt-qp-close').click();
  await header.click();
  await expect(table).toBeHidden();
});

test('Simulator GEX switches between live baseline and scenario-adjusted modes', async ({ page }) => {
  await openDashboard(page);
  const scope = page.locator('#sim-gex-scope');
  await expect(scope).toHaveText('(Live Baseline)');
  await page.locator('#sim-iv-slider').evaluate((slider) => {
    slider.value = String(Number(slider.value) + Number(slider.step || 1));
    slider.dispatchEvent(new Event('input', { bubbles:true }));
  });
  await expect(scope).toHaveText('(Scenario-Adjusted)');
  // The simulator disclosure starts collapsed in the production layout;
  // exercise the same public action used by its Reset to Live button.
  await page.evaluate(() => window.resetScenario());
  await expect(scope).toHaveText('(Live Baseline)');
  await expect(page.locator('#sim-gex-title')).toHaveText('Live Net GEX Profile ($B)');
});

test('compact dashboard keeps primary navigation within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await openDashboard(page);
  await expect(page.locator('#sec-nav-bar')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});

test('narrow dashboard starts rail disclosures collapsed', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 700 });
  await openDashboard(page);
  await expect(page.locator('#rail-context-menu')).not.toHaveAttribute('open', '');
  await expect(page.locator('#rail-tools-menu')).not.toHaveAttribute('open', '');
  const navHeight = await page.locator('#sec-nav-bar').evaluate(el => el.getBoundingClientRect().height);
  expect(navHeight).toBeLessThan(150);
});
