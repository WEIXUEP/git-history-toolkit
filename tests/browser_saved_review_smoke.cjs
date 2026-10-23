// Owns a disposable fixture; never submits CLEANUP or any production approval.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const script = `
from unittest.mock import patch
from tests.test_saved_audit import SavedAuditTests
from git_history_toolkit import main
case = SavedAuditTests()
case.setUp()
try:
    with patch('src.html_review.confirm_thresholds', side_effect=AssertionError('No threshold page')), patch('src.html_review.historical_presence', side_effect=AssertionError('No history scan')):
        main(['review', '--report', str(case.report), '--no-open-browser', '--review-timeout', '60'])
finally:
    case.doCleanups()
`;
  const child = spawn(process.argv[2], ['-u', '-c', script], {cwd: path.resolve(__dirname, '..')});
  let output = '';
  const ended = new Promise(resolve => child.on('exit', resolve));
  child.stderr.on('data', chunk => { output += chunk; });
  let browser;
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`No review URL: ${output}`)), 30000);
      child.stdout.on('data', chunk => {
        output += chunk;
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+\/review\/[\w-]+/);
        if (match) { clearTimeout(timer); resolve(match[0]); }
      });
      child.on('exit', code => { clearTimeout(timer); reject(new Error(`Exited ${code}: ${output}`)); });
    });
    browser = await chromium.launch({headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge'});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.locator('#rows tr').first().waitFor();
    assert.match(await page.locator('#saved-status').textContent(), /Saved audit:/);
    assert.equal(await page.locator('#saved-status').isVisible(), true);
    assert.equal(await page.locator('#rows input:checked').count(), 0);
    assert.equal(await page.locator('#token').inputValue(), '');
    assert.equal(await page.locator('#confirm').isEnabled(), false);
    assert.equal(await page.locator('#size').count(), 0);
    fs.mkdirSync(process.argv[3], {recursive: true});
    await page.screenshot({path: path.join(process.argv[3], 'saved-review-desktop.png'), fullPage: true});
    await page.setViewportSize({width: 1100, height: 800});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: path.join(process.argv[3], 'saved-review-compact.png'), fullPage: true});
    assert.deepEqual(errors, []);
    await page.click('#cancel');
    assert.equal(await ended, 0, output);
    console.log('Saved table opens directly, dates/banner render, selection starts empty; cancelled without scanning.');
  } finally {
    if (browser) await browser.close();
    child.kill();
    await ended;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
