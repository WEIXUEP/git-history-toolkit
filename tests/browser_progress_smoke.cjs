// Owns a disposable repository. Never point this consent simulation at user data.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');
const python = process.argv[2];
const screenshots = process.argv[3];

async function scenario(browser, fail) {
  const script = `
import time
from unittest.mock import patch
from tests.test_html_review import BrowserReviewTests
from src.html_review import ReviewSession
from src.auditor import GitLargeObjectAuditor
from git_history_auditor import main
case = BrowserReviewTests()
case.setUp()
original_progress = ReviewSession.update_progress
def progress(session, stage, completed=None, total=None, detail=''):
    original_progress(session, stage, completed, total, detail)
    if stage in ('Object history', 'Historical snapshots'):
        time.sleep(.8)
def invoke():
    main(['--repo', str(case.repo), '--min-size-mb', '.0001', '--min-age-days', '0',
          '--output-dir', str(case.root / 'live-reports'), '--no-open-browser',
          '--review-timeout', '60', '--no-cache'])
try:
    with patch.object(ReviewSession, 'update_progress', progress):
        if ${fail ? 'True' : 'False'}:
            with patch.object(GitLargeObjectAuditor, 'run', side_effect=ValueError('Synthetic scan failure')):
                try:
                    invoke()
                except SystemExit as exc:
                    if exc.code != 2:
                        raise
        else:
            invoke()
finally:
    case.doCleanups()
`;
  const child = spawn(python, ['-u', '-c', script], {cwd: path.resolve(__dirname, '..')});
  let output = '';
  child.stderr.on('data', chunk => { output += chunk; });
  const ended = new Promise(resolve => child.on('exit', resolve));
  const url = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('No threshold URL: ' + output)), 30000);
    child.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/Browser review \(thresholds\): (http:\/\/[^\s]+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    child.on('exit', code => { clearTimeout(timer); reject(new Error(`Worker exited ${code}: ${output}`)); });
  });
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (/Content Security Policy|violates.*directive|Refused to/i.test(message.text())) errors.push(message.text()); });
  try {
    await page.goto(url);
    assert.equal(await page.locator('#start').isEnabled(), false);
    await page.check('#manual');
    await page.click('#start');
    if (fail) {
      await page.waitForFunction(() => document.querySelector('#stage').textContent === 'Analysis failed');
      assert.match(await page.locator('#detail').textContent(), /Synthetic scan failure/);
      assert.match(await page.locator('#counts').textContent(), /No cleanup approved/);
      assert.equal(await page.locator('#progress').isVisible(), false);
      await page.screenshot({path: path.join(screenshots, 'progress-failure.png')});
    } else {
      await page.waitForFunction(() => document.querySelector('#stage').textContent === 'Object history' && document.querySelector('#progress').value > 0);
      assert.ok(await page.locator('#progress').getAttribute('max'));
      await page.screenshot({path: path.join(screenshots, 'progress-history.png')});
      await page.reload();
      assert.equal(await page.locator('#form').isVisible(), false);
      await page.waitForFunction(() => document.querySelector('#stage').textContent === 'Historical snapshots');
      await page.screenshot({path: path.join(screenshots, 'progress-snapshots.png')});
      await page.locator('#rows tr').first().waitFor();
      assert.equal(page.url(), url);
      assert.equal(await page.locator('#rows input:checked').count(), 0);
      assert.equal(await page.locator('#confirm').isEnabled(), false);
      await page.click('#cancel');
      await page.waitForFunction(() => document.querySelector('#finish').textContent.includes('Cancelled'));
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    const exit = await ended;
    assert.equal(exit, 0, output);
  } finally {
    await page.close();
    if (child.exitCode === null) child.kill();
    await ended;
  }
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.BROWSER_CHANNEL || undefined});
  try {
    await scenario(browser, false);
    await scenario(browser, true);
    console.log('Live history/snapshot counts, reload, same-tab review, failure display and consent gating passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
