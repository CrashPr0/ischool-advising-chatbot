"""Exercise the Canvas disclosure with real Tab navigation, entirely offline."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
from bs4 import BeautifulSoup

import app as app_module


@pytest.mark.parametrize("width", [320, 360, 400])
def test_canvas_topics_close_when_keyboard_focus_leaves(tmp_path, width):
    node = shutil.which("node")
    chrome = os.environ.get("CANVAS_TEST_CHROME") or shutil.which("google-chrome")
    if not node or not chrome:
        pytest.skip("Canvas keyboard checks need Node and Chrome (or CANVAS_TEST_CHROME)")
    package = subprocess.run(
        [node, "-p", "require.resolve('playwright-core')"],
        capture_output=True, text=True, check=False,
    )
    if package.returncode:
        pytest.skip("Canvas keyboard checks need playwright-core resolvable by Node")

    root = Path(__file__).resolve().parents[1]
    soup = BeautifulSoup(
        app_module.app.test_client().get("/?embed=canvas").data, "html.parser"
    )
    # Load the real template/layout/disclosure without application startup or APIs.
    for element in soup.select("script, link, img"):
        element.decompose()
    for filename in ("style.css", "mobile-layout.css", "canvas-mode.css"):
        style = soup.new_tag("style")
        style.string = (root / "static" / filename).read_text()
        soup.head.append(style)
    script = soup.new_tag("script")
    script.string = (root / "static/canvas-mode.js").read_text()
    soup.body.append(script)
    page_path = tmp_path / "canvas.html"
    page_path.write_text(str(soup))
    runner = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.argv[1]);
(async () => {
  const browser = await chromium.launch({
    executablePath: process.argv[2], headless: true, args: ['--no-sandbox']
  });
  try {
    const page = await browser.newPage({viewport: {width: Number(process.argv[4]), height: 700}});
    // Even CSS background requests are blocked; no server or external assets.
    await page.route('**/*', route => route.abort());
    await page.setContent(fs.readFileSync(process.argv[3], 'utf8'));
    const toggle = page.locator('#canvas-topics-toggle');
    const topics = page.locator('#topic-list .topic-btn');
    const closed = () => page.locator('#topic-list').evaluate(el => el.hidden);
    const expanded = () => toggle.getAttribute('aria-expanded');
    const focused = locator => locator.evaluate(el => el === document.activeElement);

    await toggle.focus();
    await page.keyboard.press('Enter');
    assert.equal(await expanded(), 'true');
    await page.keyboard.press('Tab');
    assert.equal(await focused(topics.first()), true);
    await page.keyboard.press('Tab');
    assert.equal(await closed(), false, 'Tab within the topic list keeps it open');

    await topics.last().focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() =>
      document.activeElement !== document.body &&
      !document.querySelector('.topics-card').contains(document.activeElement)), true);
    assert.equal(await closed(), true, 'Tab out must dismiss the overlay');
    assert.equal(await expanded(), 'false');

    await toggle.focus();
    await page.keyboard.press('Space');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await focused(toggle), true);
    assert.equal(await closed(), false, 'Returning to the toggle keeps it open');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await closed(), true, 'Shift+Tab out must dismiss the overlay');
    assert.equal(await expanded(), 'false');

    await toggle.focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Escape');
    assert.equal(await closed(), true);
    assert.equal(await focused(toggle), true, 'Escape restores focus to the toggle');

    await toggle.click();
    await topics.first().click();
    assert.equal(await closed(), true);
    assert.equal(await focused(toggle), true, 'Selection restores focus to the toggle');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
"""
    completed = subprocess.run(
        [node, "-e", runner, package.stdout.strip(), chrome, str(page_path), str(width)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
