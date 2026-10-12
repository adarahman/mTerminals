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

test('Trade panel places a compact Order action in its header', async ({ page }) => {
  await openDashboard(page);
  await page.locator('button[data-primary-nav="trade"]').evaluate(button => button.click());

  const panel = page.locator('#pt-portfolio-panel');
  await expect(panel).toHaveClass(/open/);
  await expect(panel.locator('#pt-portfolio-mode-badge')).toHaveCount(0);

  const actions = panel.locator('#trade-context-actions');
  expect(await actions.evaluate(el => el.parentElement.classList.contains('pt-portfolio-header'))).toBe(true);

  const orderButton = actions.locator('#pt-order-toggle-btn');
  const buttonLayout = await orderButton.evaluate(el => ({
    flexDirection: getComputedStyle(el).flexDirection,
    height: el.getBoundingClientRect().height,
  }));
  expect(buttonLayout.flexDirection).toBe('row');
  expect(buttonLayout.height).toBeLessThan(50);

  await orderButton.click();
  await expect(page.locator('#pt-order-panel')).toHaveClass(/open/);
  await expect(page.locator('#pt-mode-toggle')).toHaveText(/PAPER/);
});

test('sticky trade plan shows backend triggers and unavailable risk levels', async ({ page }) => {
  const sessionDate = new Intl.DateTimeFormat('en-CA', { timeZone:'Asia/Kolkata', year:'numeric', month:'2-digit', day:'2-digit' }).format(new Date());
  const [year, month, day] = sessionDate.split('-').map(Number);
  const marketOpen = Date.UTC(year, month - 1, day, 3, 45);
  const historyRows = [
    { t:marketOpen, o:24800, h:24810, l:24790, c:24805, v:10 },
    { t:marketOpen + 15 * 60_000, o:24805, h:24820, l:24800, c:24815, v:20 },
    { t:marketOpen + 30 * 60_000, o:24815, h:24830, l:24810, c:24825, v:30 },
  ];
  await page.route('**/api/**', route => {
    const body = route.request().url().includes('/api/history') ? historyRows : {};
    return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(body) });
  });
  await page.routeWebSocket('**/ws', () => {});
  await openDashboard(page);
  await page.evaluate(() => window.parseAndRender(JSON.stringify({
    symbol:'NIFTY', expiry:'31-DEC-2099', spot:24800, atm:24800,
    chain:[{ strike:24800, ceLTP:100, peLTP:100, ceOI:1000, peOI:1000 }],
    greeks:[], oiVelocity:[], lastUpdated:new Date().toISOString(),
    decision:{
      tradeability:'NO_TRADE', tradeabilityReason:'Compression with conflicting evidence',
      bias:'RANGE', confidence:38,
      confirmationLevels:{ aboveResistance:25000, belowSupport:24600 },
    },
  })));

  const plan = page.locator('#decision-sticky-plan');
  await expect(plan).toContainText('NO TRADE');
  await expect(plan).toContainText('RANGE');
  await expect(plan).toContainText('Above 25,000');
  await expect(plan).toContainText('Below 24,600');
  await expect(plan).toContainText('Compression with conflicting evidence');
  await expect(plan).toContainText('Unavailable');
  await expect(plan).toContainText('Session high / low');
  await expect(plan).toContainText('24,830.00 / 24,790.00');
  await expect(plan).toContainText('Opening range · 09:15-09:45');
  await expect(plan).toContainText('24,790.00 - 24,820.00');
  await expect(plan).toContainText('Session VWAP');
  expect(await plan.evaluate(el => getComputedStyle(el).position)).toBe('sticky');
  expect(await page.evaluate(() => {
    const decision = document.querySelector('#sec-decision');
    const actionPlan = document.querySelector('#decision-sticky-plan');
    return !!(decision.compareDocumentPosition(actionPlan) & Node.DOCUMENT_POSITION_FOLLOWING);
  })).toBe(true);
  expect(await page.evaluate(() => window._chainRange)).toBe(5);
  const stickyTop = await plan.evaluate(el => parseFloat(getComputedStyle(el).top));
  await page.evaluate(() => {
    const spacer = document.createElement('div');
    spacer.style.height = '1200px';
    document.querySelector('#dashboard').append(spacer);
    window.scrollBy(0, 900);
  });
  await expect.poll(() => plan.evaluate(el => el.getBoundingClientRect().top)).toBeGreaterThanOrEqual(stickyTop - 2);
  await expect.poll(() => plan.evaluate((el, top) => Math.abs(el.getBoundingClientRect().top - top), stickyTop)).toBeLessThan(3);
});

test('selected option-chain range survives a page refresh', async ({ page }) => {
  await openDashboard(page);
  await page.evaluate(() => window.switchChainRange(10, null));
  expect(await page.evaluate(() => window._chainRange)).toBe(10);

  await page.reload();
  await expect(page.locator('#sec-nav-bar')).toBeVisible();
  expect(await page.evaluate(() => window._chainRange)).toBe(10);
  await expect(page.locator('#range-tabs-chain .active-range')).toHaveText('±10');
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
