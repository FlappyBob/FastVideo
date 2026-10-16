// SPDX-License-Identifier: Apache-2.0
import { getApiBaseUrl } from './api';
import type { Job } from './types';

export interface StoryCharacter {
	id: string;
	name: string;
	description: string;
	asset_ids: string[];
}
export type Transition = 'cut' | 'crossfade' | 'fade_black';
export interface ShotEdit {
	in_seconds: number;
	out_seconds: number | null;
	transition: Transition;
	transition_seconds: number;
	caption: string;
	caption_start: number;
	caption_end: number | null;
}
export const DEFAULT_EDIT: ShotEdit = {
	in_seconds: 0,
	out_seconds: null,
	transition: 'cut',
	transition_seconds: 0.5,
	caption: '',
	caption_start: 0,
	caption_end: null,
};
export interface StoryShot {
	id: string;
	title: string;
	prompt: string;
	character_ids: string[];
	asset_ids: string[];
	previous_shot_id: string | null;
	seed: number;
	edit: ShotEdit;
}
export type Tier = 'preview' | 'final';
export interface StoryRecipe {
	model_id: string;
	width: number;
	height: number;
	num_frames: number;
	fps: number;
	num_inference_steps: number;
	tier?: Tier;
	[key: string]: string | number | boolean | null | undefined;
}
export interface Soundtrack {
	asset_id: string | null;
	gain_db: number;
	duck: boolean;
	fade_out_seconds: number;
}
export interface CaptionStyle {
	font_size: number;
	position: 'bottom' | 'top';
}
export interface StoryBlueprint {
	title: string;
	brief: string;
	style: string;
	characters: StoryCharacter[];
	shots: StoryShot[];
	recipe: StoryRecipe;
	soundtrack: Soundtrack;
	captions: CaptionStyle;
}
export interface StoryAsset {
	id: string;
	name: string;
	kind?: 'image' | 'audio';
	width?: number;
	height?: number;
	seconds?: number;
	sha256: string;
}
export interface TakeObservation {
	probe: { width: number; height: number; fps: number; video_seconds: number };
	motion: { frames: number; static_seconds: number[]; jumps_at: number[] };
	flags: string[];
	keyframe_times: number[];
}
export interface StoryTake {
	job_id: string;
	fingerprint: string;
	review: 'pending' | 'accepted' | 'rejected';
	note: string;
	tier?: Tier;
	observation?: TakeObservation | null;
}
export interface TimingSummary {
	operations: Record<string, number>;
	generation_seconds: number;
	queue_wait_seconds: number;
	final_takes: number;
	preview_takes: number;
}
export interface RenderRecord {
	filename: string;
	clips: number;
	seconds: number;
	revision: number;
	render_seconds: number;
	timeline: { label: string; start: number; seconds: number; transition: string }[];
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
	timing?: TimingSummary;
	renders?: RenderRecord[];
}
export interface AgentConfig {
	image_generator_configured?: boolean;
	planner_configured: boolean;
	mock: boolean;
	models: string[];
	reference_model: string;
	caption_font?: boolean;
	tiers?: Tier[];
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
export function keyframesUrl(projectId: string, shotId: string, jobId: string) {
	return `${getApiBaseUrl()}/agent/projects/${encodeURIComponent(projectId)}/takes/${encodeURIComponent(shotId)}/${encodeURIComponent(jobId)}/keyframes`;
}
export function renderUrl(projectId: string, filename: string) {
	return `${getApiBaseUrl()}/agent/projects/${encodeURIComponent(projectId)}/renders/${encodeURIComponent(filename)}`;
}
async function uploadFile<T>(url: string, file: File, failure: string): Promise<T> {
	const form = new FormData();
	form.append('file', file);
	const response = await fetch(url, { method: 'POST', body: form });
	if (!response.ok) {
		const problem = await response.json().catch(() => ({}));
		throw new Error(problem.detail || failure);
	}
	return response.json();
}
export async function uploadAgentAsset(
	projectId: string,
	file: File,
): Promise<StoryAsset> {
	return uploadFile(
		`${getApiBaseUrl()}/agent/projects/${encodeURIComponent(projectId)}/assets`,
		file,
		'Reference upload failed',
	);
}
export async function uploadSoundtrack(
	projectId: string,
	file: File,
): Promise<StoryAsset> {
	return uploadFile(
		`${getApiBaseUrl()}/agent/projects/${encodeURIComponent(projectId)}/soundtrack`,
		file,
		'Soundtrack upload failed',
	);
}
