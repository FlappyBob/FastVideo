import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AgentPage from './page';
import { agentRequest } from '@/lib/agentApi';

vi.mock('@/lib/agentApi', () => ({
	agentRequest: vi.fn(),
	assetUrl: () => '/asset',
	keyframesUrl: () => '/keyframes',
	renderUrl: (_project: string, filename: string) => `/renders/${filename}`,
	uploadAgentAsset: vi.fn(),
	uploadSoundtrack: vi.fn(),
	DEFAULT_EDIT: {
		in_seconds: 0,
		out_seconds: null,
		transition: 'cut',
		transition_seconds: 0.5,
		caption: '',
		caption_start: 0,
		caption_end: null,
	},
}));
vi.mock('@/lib/api', () => ({
	getJobVideoUrl: () => '/video',
	mergeScene: vi.fn(),
	getMergedVideoUrl: () => '/merged',
}));
const makeProject = () => ({
	id: 'project',
	revision: 1,
	approved_revision: null,
	blueprint: {
		title: 'Lighthouse',
		brief: 'A storm clears',
		style: 'cinematic',
		characters: [],
		recipe: {
			model_id: 'FastVideo/FastVideo-FastH3-8-Step-V2',
			width: 832,
			height: 480,
			num_frames: 124,
			fps: 24,
			num_inference_steps: 9,
			tier: 'preview',
		},
		soundtrack: { asset_id: null, gain_db: -14, duck: true, fade_out_seconds: 2 },
		captions: { font_size: 44, position: 'bottom' },
		shots: [
			{
				id: 'one',
				title: 'Opening',
				prompt: 'A lighthouse at night',
				character_ids: [],
				asset_ids: [],
				previous_shot_id: null,
				seed: 1024,
				edit: {
					in_seconds: 0,
					out_seconds: null,
					transition: 'cut',
					transition_seconds: 0.5,
					caption: '',
					caption_start: 0,
					caption_end: null,
				},
			},
		],
	},
	assets: [],
	jobs: {},
	takes: {},
	selected: {},
	messages: [],
	stale_shots: [],
	timing: {
		operations: {},
		generation_seconds: 0,
		queue_wait_seconds: 0,
		final_takes: 0,
		preview_takes: 0,
	},
	renders: [],
});
let project: ReturnType<typeof makeProject>;
beforeEach(() => {
	project = makeProject();
	vi.mocked(agentRequest).mockImplementation(async (path, method, body) => {
		if (path === '/config')
			return {
				planner_configured: false,
				mock: true,
				models: [project.blueprint.recipe.model_id, 'MiniMaxAI/MiniMax-H3'],
				reference_model: 'MiniMaxAI/MiniMax-H3',
				tiers: ['preview', 'final'],
				caption_font: true,
			};
		if (path === '/projects') return [project];
		if (path.endsWith('/approve')) {
			project = { ...project, approved_revision: 1 as never };
			return project;
		}
		if (method === 'PUT') {
			project = {
				...project,
				blueprint: (body as { blueprint: typeof project.blueprint }).blueprint,
				revision: 2,
			};
			return project;
		}
		return project;
	});
});

describe('Agent workspace', () => {
	it('labels simulation and does not pretend an unconfigured planner is available', async () => {
		render(<AgentPage />);
		expect(await screen.findByText(/Simulated generation/)).toBeInTheDocument();
		expect(
			await screen.findByDisplayValue('A lighthouse at night'),
		).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Plan or revise' }),
		).toBeDisabled();
		expect(screen.getByText(/Planner is not configured/)).toBeInTheDocument();
	});
	it('dirty edits cannot be generated before saving and approving', async () => {
		render(<AgentPage />);
		fireEvent.change(await screen.findByDisplayValue('A lighthouse at night'), {
			target: { value: 'A lighthouse at dawn' },
		});
		expect(
			screen.getByRole('button', { name: 'Generate missing shots' }),
		).toBeDisabled();
		expect(
			screen.getByRole('button', { name: 'Approve storyboard' }),
		).toBeDisabled();
		fireEvent.click(screen.getByRole('button', { name: 'Save storyboard' }));
		await waitFor(() =>
			expect(agentRequest).toHaveBeenCalledWith(
				'/projects/project',
				'PUT',
				expect.objectContaining({
					revision: 1,
					blueprint: expect.objectContaining({
						shots: [
							expect.objectContaining({ prompt: 'A lighthouse at dawn' }),
						],
					}),
				}),
			),
		);
	});
	it('generation is tied to the approved revision and explicit budget', async () => {
		render(<AgentPage />);
		await screen.findByDisplayValue('A lighthouse at night');
		fireEvent.click(screen.getByRole('button', { name: 'Approve storyboard' }));
		await waitFor(() =>
			expect(
				screen.getByRole('button', { name: 'Generate missing shots' }),
			).toBeEnabled(),
		);
		fireEvent.click(
			screen.getByRole('button', { name: 'Generate missing shots' }),
		);
		await waitFor(() =>
			expect(agentRequest).toHaveBeenCalledWith(
				'/projects/project/generate',
				'POST',
				{ revision: 1, max_new_jobs: 3 },
			),
		);
	});
});

it('keeps an invalid settings shape out of the editable storyboard', async () => {
	render(<AgentPage />);
	await screen.findByDisplayValue('A lighthouse at night');
	fireEvent.change(screen.getByLabelText('Recipe JSON'), {
		target: { value: 'null' },
	});
	expect(
		screen.getByText('Finish the JSON before saving.'),
	).toBeInTheDocument();
	expect(
		screen.getByRole('button', { name: 'Save storyboard' }),
	).toBeDisabled();
});

it('does not discard unsaved work by creating a new story', async () => {
	render(<AgentPage />);
	fireEvent.change(await screen.findByDisplayValue('A lighthouse at night'), {
		target: { value: 'Unsaved edit' },
	});
	expect(screen.getByRole('button', { name: /^New story$/ })).toBeDisabled();
});

it('switching the render tier is a saved storyboard change, not a hidden setting', async () => {
	render(<AgentPage />);
	await screen.findByDisplayValue('A lighthouse at night');
	fireEvent.change(screen.getByLabelText('Render tier'), {
		target: { value: 'final' },
	});
	expect(
		(screen.getByLabelText('Recipe JSON') as HTMLTextAreaElement).value,
	).toContain('"tier": "final"');
	fireEvent.click(screen.getByRole('button', { name: 'Save storyboard' }));
	await waitFor(() =>
		expect(agentRequest).toHaveBeenCalledWith(
			'/projects/project',
			'PUT',
			expect.objectContaining({
				blueprint: expect.objectContaining({
					recipe: expect.objectContaining({ tier: 'final' }),
				}),
			}),
		),
	);
});

it('renders a cut only from accepted takes and shows the timeline', async () => {
	project.jobs = { job1: { id: 'job1', status: 'completed' } as never };
	project.selected = { one: 'job1' };
	project.takes = {
		one: [
			{
				job_id: 'job1',
				fingerprint: 'f',
				review: 'accepted',
				note: '',
				tier: 'preview',
				observation: {
					probe: { width: 544, height: 320, fps: 24, video_seconds: 5.17 },
					motion: { frames: 124, static_seconds: [0, 1, 2, 3], jumps_at: [] },
					flags: ['mostly_static'],
					keyframe_times: [0, 1],
				},
			},
		],
	};
	vi.mocked(agentRequest).mockImplementation(async (path, method) => {
		if (path === '/config')
			return { planner_configured: false, mock: true, models: [], reference_model: 'x' };
		if (path === '/projects') return [project];
		if (path.endsWith('/cut/render') && method === 'POST')
			return {
				filename: 'demo-cut.mp4',
				clips: 1,
				seconds: 4.5,
				revision: 1,
				render_seconds: 2.25,
				timeline: [{ label: 'Opening', start: 0, seconds: 4.5, transition: 'cut' }],
			};
		return project;
	});
	render(<AgentPage />);
	await screen.findByDisplayValue('A lighthouse at night');
	expect(screen.getByText('Mostly static')).toBeInTheDocument();
	expect(screen.getByAltText('Key frames of Opening')).toHaveAttribute('src', '/keyframes');
	expect(screen.getByRole('button', { name: 'Propose cut' })).toBeDisabled();
	fireEvent.click(screen.getByRole('button', { name: 'Render cut' }));
	await waitFor(() =>
		expect(agentRequest).toHaveBeenCalledWith('/projects/project/cut/render', 'POST'),
	);
	expect(await screen.findByRole('link', { name: 'Download cut' })).toHaveAttribute(
		'href',
		'/renders/demo-cut.mp4',
	);
	expect(screen.getByText(/0\.0 s Opening/)).toBeInTheDocument();
});
