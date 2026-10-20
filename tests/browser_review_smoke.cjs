// Read-only visual/interaction check against an explicitly supplied review URL.
const { chromium } = require('playwright');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && /Content Security Policy|violates.*directive|Refused to/i.test(message.text())) errors.push(message.text()); });
    await page.goto(process.argv[2]);
    await page.locator('#rows tr').first().waitFor();
    assert.equal(await page.locator('#confirm').isEnabled(), false);
    assert.equal(await page.locator('#rows input:checked').count(), 0);
    assert.ok(await page.locator('#rows tr').count() > 0);
    await page.selectOption('#suffix', '.bin');
    await page.click('#select');
    assert.ok(await page.locator('#rows input:checked').count() > 0);
    await page.fill('#token', 'REVIEW');
    assert.equal(await page.locator('#confirm').isEnabled(), true);
    await page.screenshot({path: path.join(process.argv[3], 'review-desktop.png'), fullPage: true});
    assert.ok(await page.locator('#rows tr').first().locator('td').nth(0).evaluate(el => el.getBoundingClientRect().width < 90));
    await page.selectOption('#suffix', '');
    await page.click('#clear');
    assert.equal(await page.locator('#rows input:checked').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(process.argv[3], 'review-mobile.png')});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    if (process.argv[4] === '--save') {
      await page.click('#select');
      await page.fill('#token', 'REVIEW');
      await page.click('#confirm');
      await page.waitForFunction(() => document.querySelector('#finish').textContent.includes('未执行清理'));
    } else {
      await page.click('#cancel');
      await page.waitForFunction(() => document.querySelector('#finish').textContent.includes('已取消'));
    }
    console.log('Desktop/mobile, suffix selection, confirmation gating and review decision passed.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
