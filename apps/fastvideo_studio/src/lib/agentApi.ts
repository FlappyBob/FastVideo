// SPDX-License-Identifier: Apache-2.0
import { getApiBaseUrl } from './api';
import type { Job } from './types';

export interface StoryCharacter {
	id: string;
	name: string;
	description: string;
	asset_ids: string[];
}
export interface StoryShot {
	id: string;
	title: string;
	prompt: string;
	character_ids: string[];
	asset_ids: string[];
	previous_shot_id: string | null;
	seed: number;
}
export interface StoryRecipe {
	model_id: string;
	width: number;
	height: number;
	num_frames: number;
	fps: number;
	num_inference_steps: number;
	[key: string]: string | number | boolean | null;
}
export interface StoryBlueprint {
	title: string;
	brief: string;
	style: string;
	characters: StoryCharacter[];
	shots: StoryShot[];
	recipe: StoryRecipe;
}
export interface StoryAsset {
	id: string;
	name: string;
	width: number;
	height: number;
	sha256: string;
}
export interface StoryTake {
	job_id: string;
	fingerprint: string;
	review: 'pending' | 'accepted' | 'rejected';
	note: string;
}
export interface StoryProject {
	id: string;
	revision: number;
	approved_revision: number | null;
	blueprint: StoryBlueprint;
	assets?: StoryAsset[];
	jobs?: Record<string, Job | null>;
	takes: Record<string, StoryTake[]>;
	selected: Record<string, string>;
	stale_shots: string[];
	messages: { role: string; content: string; revision: number }[];
}
export interface AgentConfig {
	image_generator_configured?: boolean;
	planner_configured: boolean;
	mock: boolean;
	models: string[];
	reference_model: string;
}

export async function agentRequest<T = unknown>(
	path: string,
	method = 'GET',
	body?: unknown,
): Promise<T> {
	const response = await fetch(`${getApiBaseUrl()}/agent${path}`, {
		method,
		...(body === undefined
			? {}
			: {
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(body),
				}),
	});
	if (!response.ok) {
		const problem = await response.json().catch(() => ({}));
		throw new Error(
			typeof problem.detail === 'string'
				? problem.detail
				: `Request failed (${response.status})`,
		);
	}
	return response.json();
}
export function assetUrl(projectId: string, assetId: string) {
	return `${getApiBaseUrl()}/agent/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`;
}
export async function uploadAgentAsset(
	projectId: string,
	file: File,
): Promise<StoryAsset> {
	const form = new FormData();
	form.append('file', file);
	const response = await fetch(
		`${getApiBaseUrl()}/agent/projects/${encodeURIComponent(projectId)}/assets`,
		{ method: 'POST', body: form },
	);
	if (!response.ok) {
		const problem = await response.json().catch(() => ({}));
		throw new Error(problem.detail || 'Reference upload failed');
	}
	return response.json();
}
