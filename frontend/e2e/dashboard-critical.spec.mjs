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

test('Price Chart opens as a native dashboard modal', async ({ page }) => {
  await openDashboard(page);
  await page.getByRole('button', { name:'Open Price Chart' }).click();
  const modal=page.locator('#price-chart-modal');
  await expect(modal).toHaveClass(/open/);
  await expect(page.locator('#price-chart-canvas')).toBeVisible();
  const oneDay=page.locator('#pc-win-bar').getByRole('button',{name:'1D',exact:true});
  await expect(oneDay).toHaveClass(/pc-active/);
  expect(await page.locator('#pc-range-select').evaluate(select=>select.value)).toBe('1m');
  await oneDay.click();
  await expect(oneDay).toHaveClass(/pc-active/);
  await page.locator('#pc-range-select').selectOption('5m');
  await expect(oneDay).toHaveClass(/pc-active/);
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('priceChartSettings.v2')).windowKey)).toBe('1D');
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('priceChartSettings.v2')).range)).toBe('5m');
  const windowState=await page.evaluate(()=>({
    range:window.priceChart._windowHistoryRange,
    span:window.priceChart._zoomEnd-window.priceChart._zoomStart,
  }));
  expect(windowState.range).toBe('5m');
  expect(windowState.span).toBeGreaterThan(0);
  expect(windowState.span).toBeLessThanOrEqual(6.25*60*60*1000);
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('priceChartSettings.v2')).windowKey)).toBe('1D');
  await page.keyboard.press('Escape');
  await expect(modal).not.toHaveClass(/open/);
});

test('Escape closes dashboard flyouts one at a time', async ({ page }) => {
  await openDashboard(page);
  const toolsMenu = page.locator('#rail-tools-menu');
  if(!(await toolsMenu.evaluate(el => el.open))) await page.locator('#rail-tools-toggle').click();
  await page.locator('#pt-order-toggle-btn').click();
  await expect(page.locator('#pt-order-panel')).toHaveClass(/open/);
  await page.keyboard.press('Escape');
  await expect(page.locator('#pt-order-panel')).not.toHaveClass(/open/);
  await expect(page.locator('#pt-order-toggle-btn')).not.toHaveClass(/active/);

  await page.locator('#algo-toggle-btn').click();
  await expect(page.locator('#algo-panel')).toHaveClass(/open/);
  await page.keyboard.press('Escape');
  await expect(page.locator('#algo-panel')).not.toHaveClass(/open/);

  await page.locator('#ctrl-sidebar-toggle-btn').click();
  await expect(page.locator('#ctrl-sidebar')).toHaveClass(/open/);
  await page.keyboard.press('Escape');
  await expect(page.locator('#ctrl-sidebar')).not.toHaveClass(/open/);
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
  await expect(page.locator('#single-strike-detail-modal')).toHaveClass(/open/);
  await expect(page.locator('#strike-detail-report-content .sdr-hero')).toBeVisible();
});

test('Institutional Activity Crux opens the full multi-strike report', async ({ page }) => {
  await openDashboard(page);
  await page.getByRole('button', { name:'Open full multi-strike Institutional Activity report' }).click();
  const modal = page.locator('#strike-detail-report-modal');
  await expect(modal).toHaveClass(/open/);
  await expect(modal).toContainText('Total OI (Near ATM)');
  await expect(modal).toContainText('Smart Money');
  await expect(modal).toContainText('Market Structure');
  await expect(modal.locator('.sdt-row').first()).toBeVisible();
  await expect(page.locator('#single-strike-detail-modal')).not.toHaveClass(/open/);
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
  await expect(table.getByRole('columnheader', { name: /Signal/ })).toBeVisible();
  await page.locator('#option-chain-modal [data-chain-view="greeks"]').click();
  await expect(table.getByRole('columnheader', { name: /Delta/ })).toBeVisible();
  await expect(table.getByRole('columnheader', { name: /Vega/ })).toBeVisible();
  await table.locator('.oc-ledger-row td.ltp.ce button').first().click();
  await expect(page.locator('#pt-quick-popover')).toBeVisible();
  await page.locator('#pt-quick-popover .pt-qp-close').click();
  await page.keyboard.press('Escape');
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
