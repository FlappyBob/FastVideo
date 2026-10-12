import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import JobQueue from '@/components/jobs/JobQueue';
import { getJobsList } from '@/lib/api';
import type { Job, JobType } from '@/lib/types';
import { setActiveJobId } from '@/stores/activeJob';
import { triggerRefresh } from '@/stores/jobsRefresh';
import { makeJob as makeBaseJob } from '@/test/factories';

vi.mock('@/lib/api', () => ({
  getJobsList: vi.fn(),
  startJob: vi.fn(),
  stopJob: vi.fn(),
  deleteJob: vi.fn(),
  downloadJobVideo: vi.fn(),
}));

const makeJob = (overrides: Partial<Job> = {}): Job =>
  makeBaseJob({
    model_id: 'Wan2.1-T2V',
    status: 'completed',
    created_at: 1_700_000_000,
    num_inference_steps: 50,
    num_frames: 81,
    height: 480,
    width: 832,
    guidance_scale: 5,
    seed: 42,
    num_gpus: 1,
    ...overrides,
  });

beforeEach(() => {
  setActiveJobId(null);
  vi.mocked(getJobsList).mockResolvedValue([]);
});

describe('JobQueue', () => {
  it('shows a loading placeholder before the initial request settles', async () => {
    let resolveJobs: (jobs: Job[]) => void = () => {};
    vi.mocked(getJobsList).mockReturnValue(
      new Promise<Job[]>((resolve) => {
        resolveJobs = resolve;
      }),
    );

    render(<JobQueue jobType="inference" />);

    expect(screen.getByLabelText('Loading jobs')).toBeInTheDocument();
    expect(
      screen.queryByText('No inference jobs yet. Create one above.'),
    ).not.toBeInTheDocument();

    act(() => resolveJobs([]));
    expect(
      await screen.findByText('No inference jobs yet. Create one above.'),
    ).toBeInTheDocument();
  });

  it('shows request failures separately from an empty queue and retries', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(getJobsList).mockRejectedValueOnce(new Error('network down'));
    render(<JobQueue jobType="inference" />);

    expect(
      await screen.findByText(/Could not load jobs from the Studio API/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('No inference jobs yet. Create one above.'),
    ).not.toBeInTheDocument();

    vi.mocked(getJobsList).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole('button', { name: 'Try Again' }));
    expect(
      await screen.findByText('No inference jobs yet. Create one above.'),
    ).toBeInTheDocument();
  });

  it('shows an empty placeholder and fetches for the single job type', async () => {
    render(<JobQueue jobType="inference" />);
    expect(
      await screen.findByText('No inference jobs yet. Create one above.'),
    ).toBeInTheDocument();
    expect(getJobsList).toHaveBeenCalledWith('inference');
  });

  it('renders a JobCard for each fetched job', async () => {
    vi.mocked(getJobsList).mockResolvedValue([
      makeJob({ id: 'a', model_id: 'Model-A' }),
    ]);
    render(<JobQueue jobType="distillation" />);
    expect(await screen.findByText('Model-A')).toBeInTheDocument();
  });

  it('merges all job types when jobTypesForList is provided', async () => {
    vi.mocked(getJobsList).mockImplementation((t?: JobType) =>
      Promise.resolve(
        t === ('lora' as JobType)
          ? [makeJob({ id: 'l', model_id: 'Lora-Model', created_at: 2 })]
          : [makeJob({ id: 'f', model_id: 'Full-Model', created_at: 1 })],
      ),
    );
    render(
      <JobQueue
        jobType="finetuning"
        jobTypesForList={['finetuning', 'lora'] as JobType[]}
      />,
    );
    expect(await screen.findByText('Lora-Model')).toBeInTheDocument();
    expect(screen.getByText('Full-Model')).toBeInTheDocument();
    expect(getJobsList).toHaveBeenCalledWith('finetuning');
    expect(getJobsList).toHaveBeenCalledWith('lora');
  });

  it('refetches when a refresh is triggered', async () => {
    render(<JobQueue jobType="inference" />);
    await waitFor(() => expect(getJobsList).toHaveBeenCalledTimes(1));
    act(() => triggerRefresh());
    await waitFor(() => expect(getJobsList).toHaveBeenCalledTimes(2));
  });
});


afterEach(() => vi.useRealTimers());

it('recovers the initial connection failure without a page reload', async () => {
  vi.useFakeTimers();
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.mocked(getJobsList).mockRejectedValueOnce(new TypeError('Failed to fetch'));
  render(<JobQueue jobType="inference" />);
  await act(async () => {});
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.queryByText(/No inference jobs yet/)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(getJobsList).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/No inference jobs yet/)).toBeInTheDocument();
  expect(consoleError).not.toHaveBeenCalled();
});

it('keeps cached jobs during a connection failure and stops retries after unmount', async () => {
  vi.useFakeTimers();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.mocked(getJobsList).mockResolvedValueOnce([makeJob({ model_id: 'Cached result' })]);
  const view = render(<JobQueue jobType="inference" />);
  await act(async () => {});
  vi.mocked(getJobsList).mockRejectedValueOnce(new TypeError('Failed to fetch'));
  await act(async () => { triggerRefresh(); });
  expect(screen.getByText('Cached result')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(/temporarily unavailable/);
  vi.mocked(getJobsList).mockResolvedValueOnce([makeJob({ model_id: 'Recovered result' })]);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText('Recovered result')).toBeInTheDocument();
  view.unmount();
  const calls = vi.mocked(getJobsList).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(getJobsList).toHaveBeenCalledTimes(calls);
});

it('cancels an active outage retry when the queue unmounts', async () => {
  vi.useFakeTimers();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.mocked(getJobsList).mockRejectedValue(new TypeError('Failed to fetch'));
  const view = render(<JobQueue jobType="inference" />);
  await act(async () => {});
  expect(screen.getByRole('alert')).toBeInTheDocument();
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(getJobsList).toHaveBeenCalledTimes(1);
});
