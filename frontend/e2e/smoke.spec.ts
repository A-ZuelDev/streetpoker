import { expect, test } from '@playwright/test';

test('loads the foundation and connects to the backend', async ({ page }) => {
  await page.goto('/');

  await expect(
    page.getByRole('heading', { name: 'StreetPoker' }),
  ).toBeVisible();
  await expect(page.getByText('Backend connected')).toBeVisible();
});
