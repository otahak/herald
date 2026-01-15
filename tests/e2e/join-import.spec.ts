import { test, expect } from '@playwright/test';

test.describe('Herald multiplayer happy path', () => {
  test('host creates, guest joins via code, import army syncs', async ({ browser }) => {
    test.skip(process.env.CI === 'true', 'Requires running backend at localhost:8000');

    // Host context
    const hostContext = await browser.newContext();
    const hostPage = await hostContext.newPage();
    await hostPage.goto('http://localhost:8000/');

    // Create game
    await hostPage.getByText('Create Game').click();
    await hostPage.getByLabel('Your Name').fill('Host');
    await hostPage.getByText('Create Game', { exact: true }).click();

    // Grab game code from modal (badge text)
    const code = await hostPage.locator('.badge').first().textContent();
    expect(code).toBeTruthy();

    // Guest context
    const guestContext = await browser.newContext();
    const guestPage = await guestContext.newPage();
    await guestPage.goto('http://localhost:8000/');
    await guestPage.getByText('Join Game').click();
    await guestPage.getByLabel('Game Code').fill(code!.trim());
    await guestPage.getByLabel('Your Name').fill('Guest');
    await guestPage.getByText('Join Game', { exact: true }).click();

    // Guest lands on board, modal closes
    await expect(guestPage.locator('.game-layout')).toBeVisible();

    // Host sees guest joined
    await expect(hostPage.getByText('Guest')).toBeVisible();

    // Army import: enter a fake link (requires backend stub/fake or real)
    await guestPage.getByPlaceholder('Paste share link...').fill('https://army-forge.onepagerules.com/api/tts?id=FAKE');
    await guestPage.getByText('Import Army').click();

    // Host eventually sees units (requires backend support)
    await hostPage.waitForTimeout(1000);
    await expect(hostPage.locator('.unit-card').first()).toBeVisible({ timeout: 5000 });

    await hostContext.close();
    await guestContext.close();
  });
});
