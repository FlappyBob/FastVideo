import { expect, test } from '@playwright/test';
import { API_BASE, skipWithoutMock } from './helpers';

skipWithoutMock();
test('edits, duplicates, starts, stops and deletes a job through the UI', async ({ page, request }) => {
  await request.put(API_BASE + '/settings', { data: { autoStartJob: false } });
  const original = await (await request.post(API_BASE + '/jobs', { data: {
    name: 'lifecycle regression', model_id: 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers', prompt: 'lifecycle before edit',
    attention_backend: 'TORCH_SDPA', vsa_tile_size: 64,
  } })).json();
  let copiedId: string | undefined;
  try {
    await page.goto('/inference');
    let card = page.getByRole('article').filter({ hasText: original.prompt });
    await card.getByRole('button', { name: 'Edit', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('Attention Backend')).toHaveValue('TORCH_SDPA');
    await expect(dialog.getByLabel('VSA Tile Size')).toHaveValue('64');
    await dialog.getByLabel('Prompt', { exact: true }).fill('lifecycle after edit');
    await dialog.getByRole('button', { name: 'Save Changes' }).click();
    await expect(dialog).toBeHidden();
    card = page.getByRole('article').filter({ hasText: 'lifecycle after edit' });
    const copied = page.waitForResponse(r => r.url().endsWith('/duplicate') && r.request().method() === 'POST');
    await card.getByRole('button', { name: 'Duplicate', exact: true }).click();
    const copy = await (await copied).json(); copiedId = copy.id;
    expect(copy.attention_backend).toBe('TORCH_SDPA');
    expect(copy.vsa_tile_size).toBe(64);
    await expect(card).toHaveCount(2);
    const copyCard = card.filter({ hasText: copy.id.slice(0, 8) });
    await copyCard.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(copyCard.getByText('running', { exact: true })).toBeVisible();
    await copyCard.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(copyCard.getByText('stopped', { exact: true })).toBeVisible();
    page.once('dialog', d => d.accept());
    await copyCard.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(copyCard).toBeHidden();
  } finally {
    await request.delete(API_BASE + '/jobs/' + original.id);
    if (copiedId) await request.delete(API_BASE + '/jobs/' + copiedId);
  }
});
