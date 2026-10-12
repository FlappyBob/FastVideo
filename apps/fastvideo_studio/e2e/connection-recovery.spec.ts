import { expect, test } from '@playwright/test';
import { API_BASE, skipWithoutMock } from './helpers';

skipWithoutMock();
const jobsRoute = (url: URL) => url.origin === new URL(API_BASE).origin && url.pathname === '/api/jobs';

test('initial network failure identifies the URL and recovers without reload', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && message.text().includes('Failed to fetch jobs')) errors.push(message.text());
  });
  await page.route(jobsRoute, route => route.abort('failed'));
  await page.goto('/inference');
  await expect(page.getByRole('main').getByRole('alert')).toContainText(API_BASE + '/jobs');
  await expect(page.getByRole('link', { name: 'API settings' })).toBeVisible();
  await page.unroute(jobsRoute);
  await expect(page.getByRole('main').getByRole('alert')).toBeHidden({ timeout: 15000 });
  await expect(page.getByRole('article').first()).toBeVisible();
  expect(errors).toEqual([]);
});

test('outage preserves cached jobs and polling resumes', async ({ page, request }) => {
  await request.put(API_BASE + '/settings', { data: { autoStartJob: false } });
  const response = await request.post(API_BASE + '/jobs', { data: {
    model_id: 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers', prompt: 'connection recovery pending job',
  } });
  const job = await response.json();
  try {
    await page.goto('/inference');
    const card = page.getByRole('article').filter({ hasText: job.prompt });
    await expect(card).toBeVisible();
    await page.route(jobsRoute, route => route.abort('failed'));
    await expect(page.getByRole('status').filter({ hasText: 'Job updates are temporarily unavailable' })).toBeVisible();
    await expect(card).toBeVisible();
    await page.unroute(jobsRoute);
    await expect(page.getByRole('status').filter({ hasText: 'Job updates are temporarily unavailable' })).toBeHidden({ timeout: 15000 });
  } finally { await request.delete(API_BASE + '/jobs/' + job.id); }
});

test('browser API override can be corrected after a connection failure', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('fastvideo-default-options', JSON.stringify({ apiServerBaseUrl: 'http://127.0.0.1:1/api' })));
  await page.goto('/inference');
  await expect(page.getByRole('main').getByRole('alert')).toContainText('http://127.0.0.1:1/api/jobs');
  await page.evaluate(base => {
    const stored = JSON.parse(localStorage.getItem('fastvideo-default-options') ?? '{}');
    localStorage.setItem('fastvideo-default-options', JSON.stringify({ ...stored, apiServerBaseUrl: base }));
  }, API_BASE);
  await expect(page.getByRole('main').getByRole('alert')).toBeHidden({ timeout: 15000 });
  await expect(page.getByRole('article').first()).toBeVisible();
});

test('HTTP failure is not displayed as an empty queue and manual retry works', async ({ page }) => {
  await page.route(jobsRoute, route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"temporarily unavailable"}' }));
  await page.goto('/inference');
  await expect(page.getByRole('main').getByRole('alert')).toBeVisible();
  await expect(page.getByText('No inference jobs yet. Create one above.')).toBeHidden();
  await page.unroute(jobsRoute);
  await page.getByRole('button', { name: 'Try Again' }).click();
  await expect(page.getByRole('main').getByRole('alert')).toBeHidden();
});
