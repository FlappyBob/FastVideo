'use client';

import * as React from 'react';
import { CharacterLibrary } from '@/components/agent/CharacterLibrary';
import { StoryShotCard } from '@/components/agent/StoryShotCard';
import { Button } from '@/components/ui/button';
import { getMergedVideoUrl, mergeScene } from '@/lib/api';
import {
	agentRequest,
	renderUrl,
	uploadSoundtrack,
	DEFAULT_EDIT,
	type AgentConfig,
	type RenderRecord,
	type StoryBlueprint,
	type StoryProject,
	type StoryShot,
	type Tier,
} from '@/lib/agentApi';

const FIELD =
	'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-50';
const V2 = 'FastVideo/FastVideo-FastH3-8-Step-V2';

export default function AgentPage() {
	const [config, setConfig] = React.useState<AgentConfig | null>(null);
	const [projects, setProjects] = React.useState<StoryProject[]>([]);
	const [project, setProject] = React.useState<StoryProject | null>(null);
	const [draft, setDraft] = React.useState<StoryBlueprint | null>(null);
	const [dirty, setDirty] = React.useState(false);
	const [busy, setBusy] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const [instruction, setInstruction] = React.useState('');
	const [target, setTarget] = React.useState('');
	const [budget, setBudget] = React.useState(3);
	const [exportUrl, setExportUrl] = React.useState<string | null>(null);
	const [recipeText, setRecipeText] = React.useState('');
	const [recipeError, setRecipeError] = React.useState(false);
	const [cutInstruction, setCutInstruction] = React.useState('');
	const [render, setRender] = React.useState<{
		project: string;
		record: RenderRecord;
	} | null>(null);

	const install = React.useCallback((value: StoryProject) => {
		setProject(value);
		setDraft(structuredClone(value.blueprint));
		setDirty(false);
		setRecipeError(false);
		setRecipeText(JSON.stringify(value.blueprint.recipe, null, 2));
		setExportUrl(null);
		setRender((previous) =>
			value.renders?.length
				? { project: value.id, record: value.renders[value.renders.length - 1] }
				: previous?.project === value.id
					? previous
					: null,
		);
	}, []);
	const load = React.useCallback(
		async (id: string) => {
			install(await agentRequest<StoryProject>(`/projects/${id}`));
		},
		[install],
	);
	React.useEffect(() => {
		let cancelled = false;
		Promise.all([
			agentRequest<AgentConfig>('/config'),
			agentRequest<StoryProject[]>('/projects'),
		])
			.then(async ([settings, list]) => {
				if (cancelled) return;
				setConfig(settings);
				setProjects(list);
				if (list[0]) {
					const current = await agentRequest<StoryProject>(
						`/projects/${list[0].id}`,
					);
					if (!cancelled) install(current);
				}
			})
			.catch((e) => {
				if (!cancelled)
					setError(e instanceof Error ? e.message : 'Could not connect');
			});
		return () => {
			cancelled = true;
		};
	}, [install]);
	const active = Object.values(project?.jobs ?? {}).some(
		(job) => job && ['queued', 'running'].includes(job.status),
	);
	React.useEffect(() => {
		if (!project?.id || !active) return;
		let cancelled = false;
		let polling = false;
		const timer = setInterval(async () => {
			if (polling) return;
			polling = true;
			try {
				const current = await agentRequest<StoryProject>(
					`/projects/${project.id}`,
				);
				if (!cancelled) setProject(current);
			} catch (e) {
				if (!cancelled)
					setError(e instanceof Error ? e.message : 'Status update failed');
			} finally {
				polling = false;
			}
		}, 2000);
		return () => {
			cancelled = true;
			clearInterval(timer);
		};
	}, [project?.id, active]);

	async function perform(action: () => Promise<unknown>, refresh = true) {
		setBusy(true);
		setError(null);
		try {
			await action();
			if (refresh && project) await load(project.id);
			setProjects(await agentRequest<StoryProject[]>('/projects'));
		} catch (e) {
			setError(e instanceof Error ? e.message : 'Action failed');
		} finally {
			setBusy(false);
		}
	}
	function edit(change: (value: StoryBlueprint) => void) {
		if (!draft) return;
		const next = structuredClone(draft);
		change(next);
		setDraft(next);
		setDirty(true);
		setExportUrl(null);
	}
	function editShot(id: string, change: Partial<StoryShot>) {
		edit((value) => {
			value.shots = value.shots.map((shot) =>
				shot.id === id ? { ...shot, ...change } : shot,
			);
		});
	}
	const references = !!draft?.shots.some(
		(shot) =>
			shot.previous_shot_id ||
			shot.asset_ids.length ||
			shot.character_ids.some(
				(id) => draft.characters.find((c) => c.id === id)?.asset_ids.length,
			),
	);
	const unsupported =
		references && draft?.recipe.model_id !== config?.reference_model;
	const valid =
		!!draft &&
		draft.title.trim() &&
		draft.shots.every((s) => s.title.trim() && s.prompt.trim()) &&
		draft.characters.every((c) => c.name.trim() && c.description.trim()) &&
		!recipeError;
	const canExport =
		!!project?.blueprint.shots.length &&
		project.blueprint.shots.every((shot) => {
			const selected = project.selected[shot.id];
			return (
				project.jobs?.[selected]?.status === 'completed' &&
				project.takes[shot.id]?.find((t) => t.job_id === selected)?.review ===
					'accepted'
			);
		});
	async function importFile(file: File) {
		const value = JSON.parse(await file.text());
		if (!project) return;
		if (value.shot_descriptions) {
			await agentRequest(`/projects/${project.id}/import-vimax`, 'POST', {
				revision: project.revision,
				payload: value,
			});
		} else {
			await agentRequest(`/projects/${project.id}`, 'PUT', {
				revision: project.revision,
				blueprint: {
					...value,
					recipe: value.recipe ?? project.blueprint.recipe,
				},
			});
		}
	}

	return (
		<main className="mx-auto max-w-[1500px] space-y-5 p-4 md:p-7">
			<div>
				<p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
					FastVideo / creative workspace
				</p>
				<h1 className="mt-1 text-3xl font-semibold">Video Agent</h1>
				<p className="mt-2 text-sm text-muted-foreground">
					Shape a story, anchor its characters, and direct each shot.
				</p>
			</div>
			{config?.mock && (
				<div
					role="status"
					className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
				>
					Simulated generation — this demo does not run video models.
				</div>
			)}
			{error && (
				<div
					role="alert"
					className="rounded-lg border border-destructive/50 p-3 text-sm text-destructive"
				>
					{error}
				</div>
			)}
			<div className="grid gap-5 lg:grid-cols-[230px_1fr]">
				<aside className="space-y-3">
					<Button
						disabled={busy || dirty}
						className="w-full"
						onClick={() =>
							void perform(async () => {
								const created = await agentRequest<StoryProject>(
									'/projects',
									'POST',
									{ title: 'Untitled story' },
								);
								await load(created.id);
							}, false)
						}
					>
						New story
					</Button>
					{projects.map((item) => (
						<button
							key={item.id}
							disabled={busy || dirty}
							onClick={() => void perform(() => load(item.id), false)}
							className={`w-full rounded-xl border p-3 text-left ${item.id === project?.id ? 'border-accent-blue bg-accent-blue/10' : 'border-border bg-card'}`}
						>
							<span className="block text-sm font-medium">
								{item.blueprint.title}
							</span>
							<span className="text-xs text-muted-foreground">
								{item.blueprint.shots.length} shots · revision {item.revision}
							</span>
						</button>
					))}
					{dirty && (
						<p className="text-xs text-muted-foreground">
							Save your storyboard before switching projects.
						</p>
					)}
				</aside>
				{!draft || !project ? (
					<section className="rounded-xl border border-dashed p-12 text-center text-muted-foreground">
						Create a story to begin.
					</section>
				) : (
					<div className="space-y-5">
						<section className="rounded-xl border border-border bg-card p-5">
							<fieldset disabled={busy || active} className="space-y-3">
								<label className="block text-sm">
									Story title
									<input
										className={FIELD}
										value={draft.title}
										onChange={(e) =>
											edit((v) => {
												v.title = e.target.value;
											})
										}
									/>
								</label>
								<div className="grid gap-3 md:grid-cols-2">
									<label className="text-sm">
										Creative brief
										<textarea
											className={FIELD}
											rows={3}
											value={draft.brief}
											onChange={(e) =>
												edit((v) => {
													v.brief = e.target.value;
												})
											}
											placeholder="Who is this about, and what changes by the ending?"
										/>
									</label>
									<label className="text-sm">
										Visual style and continuity
										<textarea
											className={FIELD}
											rows={3}
											value={draft.style}
											onChange={(e) =>
												edit((v) => {
													v.style = e.target.value;
												})
											}
											placeholder="Lighting, palette, location, wardrobe, screen direction…"
										/>
									</label>
								</div>
							</fieldset>
							<div className="mt-4 grid gap-3 md:grid-cols-[1fr_220px]">
								<label className="text-sm">
									Direct the next change
									<textarea
										className={FIELD}
										value={instruction}
										onChange={(e) => setInstruction(e.target.value)}
										rows={2}
										placeholder="Plan three shots, or make the discovery more surprising…"
									/>
								</label>
								<div className="space-y-2">
									<label className="text-sm">
										Scope
										<select
											className={FIELD}
											value={target}
											onChange={(e) => setTarget(e.target.value)}
										>
											<option value="">Whole storyboard</option>
											{draft.shots.map((shot) => (
												<option key={shot.id} value={shot.id}>
													{shot.title}
												</option>
											))}
										</select>
									</label>
									<Button
										className="w-full"
										disabled={
											busy ||
											active ||
											dirty ||
											!instruction.trim() ||
											!config?.planner_configured
										}
										onClick={() =>
											void perform(() =>
												agentRequest(`/projects/${project.id}/plan`, 'POST', {
													revision: project.revision,
													instruction,
													target_shot: target || null,
												}),
											)
										}
									>
										Plan or revise
									</Button>
								</div>
							</div>
							{!config?.planner_configured ? (
								<p className="mt-2 text-xs text-muted-foreground">
									Planner is not configured. Import a ViMax plan or edit the
									storyboard manually.
								</p>
							) : (
								<p className="mt-2 text-xs text-muted-foreground">
									The planner reads your story and uploaded references. Proposed
									changes wait for your review.
								</p>
							)}
							{project.messages.slice(-4).map((message, i) => (
								<p
									key={i}
									className="mt-2 border-l-2 border-accent-blue/30 pl-3 text-xs text-muted-foreground"
								>
									<strong>{message.role}:</strong> {message.content}
								</p>
							))}
						</section>

						<CharacterLibrary
							{...{
								draft,
								project,
								busy,
								active,
								dirty,
								config,
								edit,
								perform,
							}}
						/>

						<section className="space-y-3">
							<div className="flex flex-wrap items-center justify-between gap-2">
								<h2 className="text-xl font-semibold">
									Storyboard{' '}
									<span className="text-sm font-normal text-muted-foreground">
										{draft.shots.length} shots
									</span>
								</h2>
								<div className="flex gap-2">
									<label className="cursor-pointer rounded-lg border px-3 py-2 text-sm">
										Import storyboard
										<input
											aria-label="Import storyboard"
											type="file"
											accept="application/json,.json"
											className="sr-only"
											disabled={busy || dirty || active}
											onChange={(e) => {
												const file = e.target.files?.[0];
												if (file) void perform(() => importFile(file));
												e.target.value = '';
											}}
										/>
									</label>
									<Button
										variant="outline"
										disabled={busy || active || draft.shots.length >= 32}
										onClick={() =>
											edit((v) => {
												v.shots.push({
													id: crypto.randomUUID(),
													title: `Shot ${v.shots.length + 1}`,
													prompt: '',
													character_ids: [],
													asset_ids: [],
													previous_shot_id: null,
													seed: 1024 + v.shots.length,
													edit: { ...DEFAULT_EDIT },
												});
											})
										}
									>
										Add shot
									</Button>
								</div>
							</div>
							<div className="grid gap-4 xl:grid-cols-2">
								{draft.shots.map((shot, index) => (
									<StoryShotCard
										key={shot.id}
										{...{
											shot,
											index,
											project,
											draft,
											busy,
											active,
											dirty,
											editShot,
											perform,
										}}
									/>
								))}
							</div>
						</section>

						<section className="rounded-xl border border-border bg-card p-5">
							<div className="grid gap-3 md:grid-cols-[1fr_180px]">
								<label className="text-sm">
									Video model
									<select
										className={FIELD}
										disabled={busy || active}
										value={draft.recipe.model_id}
										onChange={(e) => {
											const recipe = {
												...draft.recipe,
												model_id: e.target.value,
												num_inference_steps: e.target.value === V2 ? 9 : 50,
												guidance_scale: e.target.value === V2 ? 1 : 5,
												vsa_sparsity: e.target.value === V2 ? 0.8 : 0,
												attention_backend:
													e.target.value === V2 ? 'VIDEO_SPARSE_ATTN_H3' : null,
												vsa_tile_size: e.target.value === V2 ? 64 : null,
											};
											edit((v) => {
												v.recipe = recipe;
											});
											setRecipeText(JSON.stringify(recipe, null, 2));
										}}
									>
										{config?.models.map((model) => (
											<option key={model} value={model}>
												{model.split('/').pop()}
											</option>
										))}
									</select>
								</label>
								<label className="text-sm">
									New-job budget
									<input
										type="number"
										min={0}
										max={32}
										className={FIELD}
										value={budget}
										onChange={(e) => setBudget(Number(e.target.value))}
									/>
								</label>
							</div>
							<div className="mt-3 grid gap-3 md:grid-cols-[180px_1fr_160px]">
								<label className="text-sm">
									Render tier
									<select
										className={FIELD}
										disabled={busy || active}
										value={draft.recipe.tier ?? 'final'}
										onChange={(e) => {
											const recipe = {
												...draft.recipe,
												tier: e.target.value as Tier,
											};
											edit((v) => {
												v.recipe = recipe;
											});
											setRecipeText(JSON.stringify(recipe, null, 2));
										}}
									>
										<option value="preview">Preview (smaller canvas)</option>
										<option value="final">Final (full canvas)</option>
									</select>
								</label>
								<label className="text-sm">
									Soundtrack
									<div className="flex items-center gap-2">
										<select
											className={FIELD}
											disabled={busy || active}
											value={draft.soundtrack?.asset_id ?? ''}
											onChange={(e) =>
												edit((v) => {
													v.soundtrack = {
														...v.soundtrack,
														asset_id: e.target.value || null,
													};
												})
											}
										>
											<option value="">None (generated audio only)</option>
											{project.assets
												?.filter((a) => a.kind === 'audio')
												.map((a) => (
													<option key={a.id} value={a.id}>
														{a.name}
														{a.seconds ? ` · ${Math.round(a.seconds)} s` : ''}
													</option>
												))}
										</select>
										<label className="cursor-pointer whitespace-nowrap rounded-lg border px-3 py-2 text-sm">
											Upload music
											<input
												aria-label="Upload music"
												type="file"
												accept="audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg"
												className="sr-only"
												disabled={busy || dirty || active}
												onChange={(e) => {
													const file = e.target.files?.[0];
													if (file)
														void perform(() => uploadSoundtrack(project.id, file));
													e.target.value = '';
												}}
											/>
										</label>
									</div>
								</label>
								<label className="text-sm">
									Music level (dB)
									<input
										type="number"
										min={-40}
										max={6}
										step={1}
										className={FIELD}
										disabled={busy || active}
										value={draft.soundtrack?.gain_db ?? -14}
										onChange={(e) =>
											edit((v) => {
												v.soundtrack = {
													...v.soundtrack,
													gain_db: Number(e.target.value) || 0,
												};
											})
										}
									/>
								</label>
							</div>
							<p className="mt-2 text-xs text-muted-foreground">
								Preview renders the same prompts and seeds on a smaller canvas. A
								final render of a shot waits until one of its previews is
								accepted. Music is ducked under generated audio and faded out.
							</p>
							{unsupported && (
								<p role="status" className="mt-3 text-sm text-amber-600">
									Visual references require the full MiniMax H3 model. FastH3 V2
									generates independent shots; it cannot preserve these
									reference conditions.
								</p>
							)}
							<details className="mt-4">
								<summary className="cursor-pointer text-sm text-muted-foreground">
									Generation settings
								</summary>
								<label className="block text-xs">
									Recipe JSON
									<textarea
										className={`${FIELD} mt-2 font-mono`}
										rows={10}
										disabled={busy || active}
										value={recipeText}
										onChange={(e) => {
											setRecipeText(e.target.value);
											setDirty(true);
											try {
												const recipe = JSON.parse(e.target.value);
												if (
													!recipe ||
													Array.isArray(recipe) ||
													typeof recipe.model_id !== 'string' ||
													![
														'width',
														'height',
														'num_frames',
														'fps',
														'num_inference_steps',
													].every(
														(key) =>
															typeof recipe[key] === 'number' &&
															recipe[key] > 0,
													)
												)
													throw new Error('Invalid recipe');
												edit((v) => {
													v.recipe = recipe;
												});
												setRecipeError(false);
											} catch {
												setRecipeError(true);
											}
										}}
									/>
								</label>
								{recipeError && (
									<p className="text-xs text-destructive">
										Finish the JSON before saving.
									</p>
								)}
							</details>
							<div className="mt-4 flex flex-wrap gap-2">
								<Button
									variant="outline"
									disabled={busy || active || !dirty || !valid}
									onClick={() =>
										void perform(() =>
											agentRequest(`/projects/${project.id}`, 'PUT', {
												revision: project.revision,
												blueprint: draft,
											}),
										)
									}
								>
									Save storyboard
								</Button>
								<Button
									variant="outline"
									disabled={
										busy ||
										active ||
										dirty ||
										!draft.shots.length ||
										unsupported
									}
									onClick={() =>
										void perform(() =>
											agentRequest(`/projects/${project.id}/approve`, 'POST', {
												revision: project.revision,
											}),
										)
									}
								>
									Approve storyboard
								</Button>
								<Button
									disabled={
										busy ||
										dirty ||
										unsupported ||
										!draft.shots.length ||
										project.approved_revision !== project.revision ||
										!Number.isInteger(budget) ||
										budget < 0 ||
										budget > 32
									}
									onClick={() =>
										void perform(() =>
											agentRequest(`/projects/${project.id}/generate`, 'POST', {
												revision: project.revision,
												max_new_jobs: budget,
											}),
										)
									}
								>
									Generate missing shots
								</Button>
								<Button
									variant="outline"
									disabled={busy || dirty || !canExport}
									onClick={() =>
										void perform(async () => {
											const { job_ids } = await agentRequest<{
												job_ids: string[];
											}>(`/projects/${project.id}/export-jobs`);
											const merged = await mergeScene(job_ids, draft.title);
											setExportUrl(getMergedVideoUrl(merged.filename));
										}, false)
									}
								>
									Export accepted shots
								</Button>
								<Button
									disabled={busy || dirty || !canExport}
									onClick={() =>
										void perform(async () => {
											const result = await agentRequest<RenderRecord>(
												`/projects/${project.id}/cut/render`,
												'POST',
											);
											setRender({ project: project.id, record: result });
										})
									}
								>
									Render cut
								</Button>
							</div>
							<div className="mt-3 grid gap-2 md:grid-cols-[1fr_160px]">
								<input
									aria-label="Cut instruction"
									className={FIELD}
									value={cutInstruction}
									disabled={busy || dirty || !config?.planner_configured}
									onChange={(e) => setCutInstruction(e.target.value)}
									placeholder="Tighten the opening, dip to black before the last shot…"
								/>
								<Button
									variant="outline"
									disabled={
										busy || dirty || active || !config?.planner_configured
									}
									onClick={() =>
										void perform(() =>
											agentRequest(`/projects/${project.id}/cut/propose`, 'POST', {
												revision: project.revision,
												instruction: cutInstruction,
											}),
										)
									}
								>
									Propose cut
								</Button>
							</div>
							{project.timing && (
								<p className="mt-2 text-xs text-muted-foreground">
									Timing · generation {project.timing.generation_seconds} s ·
									queue wait {project.timing.queue_wait_seconds} s · planning{' '}
									{project.timing.operations.plan ?? 0} s · render{' '}
									{project.timing.operations.render ?? 0} s ·{' '}
									{project.timing.preview_takes} preview /{' '}
									{project.timing.final_takes} final takes completed
								</p>
							)}
							<p className="mt-3 text-xs text-muted-foreground">
								Revision {project.revision} ·{' '}
								{project.approved_revision === project.revision
									? 'Approved'
									: 'Needs approval'}
								{dirty ? ' · Unsaved changes' : ''}
								{active ? ' · Generation active; editing is paused' : ''}
							</p>
						</section>
						{render?.project === project.id && (
							<section className="rounded-xl border p-5">
								<h2 className="mb-3 font-semibold">
									Rendered cut{' '}
									<span className="text-sm font-normal text-muted-foreground">
										{render.record.clips} shots ·{' '}
										{render.record.seconds.toFixed(1)} s · rendered in{' '}
										{render.record.render_seconds.toFixed(1)} s
									</span>
								</h2>
								<video
									controls
									src={renderUrl(project.id, render.record.filename)}
									className="max-h-[60vh] w-full bg-black"
								/>
								<ol className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
									{render.record.timeline.map((item) => (
										<li key={`${item.label}-${item.start}`}>
											{item.start.toFixed(1)} s {item.label}
											{item.transition !== 'cut' ? ` (${item.transition})` : ''}
										</li>
									))}
								</ol>
								<a
									href={renderUrl(project.id, render.record.filename)}
									download
									className="mt-3 inline-block text-sm underline"
								>
									Download cut
								</a>
							</section>
						)}
						{exportUrl && (
							<section className="rounded-xl border p-5">
								<h2 className="mb-3 font-semibold">Raw join</h2>
								<video
									controls
									src={exportUrl}
									className="max-h-[60vh] w-full bg-black"
								/>
								<a
									href={exportUrl}
									download
									className="mt-3 inline-block text-sm underline"
								>
									Download film
								</a>
							</section>
						)}
					</div>
				)}
			</div>
		</main>
	);
}
