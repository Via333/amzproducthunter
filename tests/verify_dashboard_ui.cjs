// Local browser acceptance check; never starts a research or paid scan job.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  const output = path.resolve(process.env.AMZ_UI_OUTPUT || 'output/playwright');
  await fs.mkdir(output, {recursive: true});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.hostname === '127.0.0.1' || url.protocol === 'data:' ? route.continue() : route.abort();
      });
      await page.goto(process.env.AMZ_UI_URL || 'http://127.0.0.1:8765/?v=optimization-20261004#archive', {waitUntil: 'domcontentloaded'});
      await page.locator('#archiveStatus').waitFor();
      assert.equal(await page.locator('#archiveStatus').inputValue(), 'current');
      await page.locator('#archive').scrollIntoViewIfNeeded();
      await page.waitForTimeout(500);
      assert.ok(await page.locator('#archiveBody .pool-item').count() > 0, 'Expected visible repaired archive rows');
      await page.screenshot({path: path.join(output, `archive-${viewport.width}.png`)});
      await page.locator('#archiveStatus').selectOption('stale_needs_rescan');
      assert.ok((await page.locator('#archiveBody').innerText()).includes('待复扫'));
      await page.locator('#archiveSearch').fill('NO_SUCH_PRODUCT_FIXTURE');
      assert.equal(await page.locator('#archiveBody .pool-item').count(), 0);
      await page.locator('#archiveSearch').fill('');
      const layout = await page.evaluate(() => ({viewport: innerWidth, width: document.documentElement.scrollWidth}));
      assert.ok(layout.width <= layout.viewport + 1, `Page overflows horizontally: ${JSON.stringify(layout)}`);
      assert.deepEqual(errors, [], 'Browser JavaScript errors');
      console.log(JSON.stringify({viewport, layout, pageErrors: errors, archiveFilter: 'passed'}));
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
