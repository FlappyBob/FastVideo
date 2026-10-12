import { afterEach, expect, it, vi } from 'vitest';
import { createJob } from './api';

afterEach(() => vi.unstubAllGlobals());

it('shows the backend validation error returned when creating a job', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ detail: 'Unknown attention backend typo' }),
    { status: 400, headers: { 'Content-Type': 'application/json' } },
  )));
  await expect(createJob({ model_id: 'model', prompt: 'cat', attention_backend: 'typo' }))
    .rejects.toThrow('Unknown attention backend typo');
});

it('identifies the actual API address when listing jobs cannot connect', async () => {
  const { getJobsList } = await import('./api');
  localStorage.setItem('fastvideo-default-options', JSON.stringify({ apiServerBaseUrl: 'http://127.0.0.1:8191/api/' }));
  const fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
  vi.stubGlobal('fetch', fetch);
  try {
    await expect(getJobsList('inference')).rejects.toThrow('http://127.0.0.1:8191/api/jobs?job_type=inference');
    expect(fetch).toHaveBeenCalledTimes(1);
  } finally { localStorage.clear(); }
});
