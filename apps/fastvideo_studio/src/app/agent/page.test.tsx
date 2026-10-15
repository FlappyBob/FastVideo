import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AgentPage from './page';
import { agentRequest } from '@/lib/agentApi';

vi.mock('@/lib/agentApi', () => ({
	agentRequest: vi.fn(),
	assetUrl: () => '/asset',
	uploadAgentAsset: vi.fn(),
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
		},
		shots: [
			{
				id: 'one',
				title: 'Opening',
				prompt: 'A lighthouse at night',
				character_ids: [],
				asset_ids: [],
				previous_shot_id: null,
				seed: 1024,
			},
		],
	},
	assets: [],
	jobs: {},
	takes: {},
	selected: {},
	messages: [],
	stale_shots: [],
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
