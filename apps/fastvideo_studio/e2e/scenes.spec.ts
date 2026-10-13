import { expect, test } from '@playwright/test';
import { API_BASE, skipWithoutMock } from './helpers';

skipWithoutMock();

test('scene names, clip order and selected takes survive a page reload', async ({ page, request }) => {
  const suffix = Date.now();
  const title = `Persistent scene ${suffix}`;
  const ids: string[] = [];
  for (const name of ['coast', 'coast-alt', 'lantern']) {
    const response = await request.post(`${API_BASE}/jobs`, {data: {
      model_id: 'FastVideo/FastVideo-FastH3-8-Step-V2', prompt: 'A coastal lighthouse', name: `${name}-${suffix}`,
    }});
    expect(response.ok()).toBeTruthy();
    ids.push((await response.json()).id);
  }
  await page.goto('/scenes');
  await page.getByRole('button', {name: 'New scene', exact: true}).click();
  const editor = page.getByRole('dialog');
  await editor.getByLabel('Scene name', {exact: true}).fill(title);
  await editor.getByLabel('Add job', {exact: true}).selectOption(ids[0]);
  await editor.getByRole('button', {name: 'Add to scene'}).click();
  await editor.getByLabel('Clip 1 name', {exact: true}).fill('Coast');
  await editor.getByLabel('Add job', {exact: true}).selectOption(ids[1]);
  await editor.getByLabel('Add as', {exact: true}).selectOption('0');
  await editor.getByRole('button', {name: 'Add to scene'}).click();
  await editor.getByLabel('Add job', {exact: true}).selectOption(ids[2]);
  await editor.getByLabel('Add as', {exact: true}).selectOption('new');
  await editor.getByRole('button', {name: 'Add to scene'}).click();
  await editor.getByLabel('Clip 2 name', {exact: true}).fill('Lantern');
  await editor.getByRole('button', {name: 'Save scene', exact: true}).click();
  await expect(editor).toBeHidden();
  const scene = page.getByRole('region', {name: title, exact: true});
  await scene.getByLabel('Take for clip 1').selectOption(ids[1]);
  await expect.poll(async () => {
    const rows = await (await request.get(`${API_BASE}/scenes`)).json();
    return rows.find((row: {name: string}) => row.name === title)?.clips[0].selected_job_id;
  }).toBe(ids[1]);
  await page.reload();
  await expect(scene.getByLabel('Take for clip 1')).toHaveValue(ids[1]);
  await page.getByRole('button', {name: 'Edit scene', exact: true}).click();
  await editor.getByRole('button', {name: 'Move clip 2 up'}).click();
  await editor.getByRole('button', {name: 'Save scene', exact: true}).click();
  await expect(editor).toBeHidden();
  await page.reload();
  await expect(scene.getByLabel('Take for clip 2')).toHaveValue(ids[1]);
  const stored = (await (await request.get(`${API_BASE}/scenes`)).json()).find((row: {name: string}) => row.name === title);
  expect(stored.clips.map((clip: {name: string}) => clip.name)).toEqual(['Lantern', 'Coast']);
  await expect(scene.getByRole('checkbox', {name: 'Use last frame of clip 1', exact: true})).toBeDisabled();
});
