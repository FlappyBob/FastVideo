'use client';
import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
	agentRequest,
	keyframesUrl,
	DEFAULT_EDIT,
	type ShotEdit,
	type StoryBlueprint,
	type StoryProject,
	type StoryShot,
	type TakeObservation,
	type Transition,
} from '@/lib/agentApi';
const FIELD =
	'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-50';
import { getJobVideoUrl } from '@/lib/api';

const FLAG_LABELS: Record<string, string> = {
	mostly_static: 'Mostly static',
	possible_hidden_cut: 'Possible hidden cut',
	frame_count_mismatch: 'Frame count differs from the request',
	no_audio_track: 'No audio track',
	silent_audio: 'Audio is silent',
};

function numberOrNull(value: string): number | null {
	if (value.trim() === '') return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

export function StoryShotCard({
	shot,
	index,
	project,
	draft,
	busy,
	active,
	dirty,
	editShot,
	perform,
}: {
	shot: StoryShot;
	index: number;
	project: StoryProject;
	draft: StoryBlueprint;
	busy: boolean;
	active: boolean;
	dirty: boolean;
	editShot: (id: string, change: Partial<StoryShot>) => void;
	perform: (action: () => Promise<unknown>, refresh?: boolean) => Promise<void>;
}) {
	const selected = project.selected[shot.id];
	const job = project.jobs?.[selected];
	const take = project.takes[shot.id]?.find((t) => t.job_id === selected);
	const edit: ShotEdit = { ...DEFAULT_EDIT, ...(shot.edit ?? {}) };
	const [observation, setObservation] = React.useState<TakeObservation | null>(
		take?.observation ?? null,
	);
	React.useEffect(() => {
		setObservation(take?.observation ?? null);
	}, [take?.observation, selected]);
	React.useEffect(() => {
		if (job?.status !== 'completed' || !selected || take?.observation) return;
		let cancelled = false;
		agentRequest<TakeObservation>(
			`/projects/${project.id}/takes/${encodeURIComponent(shot.id)}/${encodeURIComponent(selected)}`,
		)
			.then((record) => {
				if (!cancelled) setObservation(record);
			})
			.catch(() => {
				/* observation is advisory; the take still plays */
			});
		return () => {
			cancelled = true;
		};
	}, [job?.status, selected, project.id, shot.id, take?.observation]);
	const seconds = observation?.probe.video_seconds;
	function setEdit(change: Partial<ShotEdit>) {
		editShot(shot.id, { edit: { ...edit, ...change } });
	}
	return (
		<article
			key={shot.id}
			className="overflow-hidden rounded-xl border border-border bg-card"
		>
			<div className="aspect-video bg-black/90">
				{job?.status === 'completed' ? (
					<video
						controls
						preload="metadata"
						src={getJobVideoUrl(job.id)}
						className="h-full w-full"
					/>
				) : (
					<div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-400">
						<span className="text-3xl font-light">
							{String(index + 1).padStart(2, '0')}
						</span>
						<span>{job?.status ?? 'Awaiting generation'}</span>
						{job?.progress_msg && (
							<span className="max-w-[90%] text-center text-xs">
								{job.progress_msg}
							</span>
						)}
					</div>
				)}
			</div>
			{job?.status === 'completed' && selected && (
				<div className="border-t border-border bg-black/80">
					<img
						alt={`Key frames of ${shot.title}`}
						src={keyframesUrl(project.id, shot.id, selected)}
						className="w-full"
						loading="lazy"
					/>
					<div className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs text-slate-300">
						<span className="rounded bg-slate-700 px-2 py-0.5 uppercase tracking-wide">
							{take?.tier ?? 'final'}
						</span>
						{seconds !== undefined && (
							<span>
								{seconds.toFixed(2)} s · {observation?.probe.width}×
								{observation?.probe.height}
							</span>
						)}
						{observation?.flags.map((flag) => (
							<span
								key={flag}
								className="rounded bg-amber-500/20 px-2 py-0.5 text-amber-200"
							>
								{FLAG_LABELS[flag] ?? flag}
							</span>
						))}
					</div>
				</div>
			)}
			<div className="space-y-3 p-4">
				<fieldset disabled={busy || active} className="space-y-3">
					<label className="text-xs">
						Shot title
						<input
							className={FIELD}
							value={shot.title}
							onChange={(e) => editShot(shot.id, { title: e.target.value })}
						/>
					</label>
					<label className="block text-xs">
						Action, framing, camera and sound
						<textarea
							className={FIELD}
							rows={4}
							value={shot.prompt}
							onChange={(e) => editShot(shot.id, { prompt: e.target.value })}
						/>
					</label>
					<div className="flex flex-wrap gap-3">
						{draft.characters.map((c) => (
							<label key={c.id} className="text-xs">
								<input
									type="checkbox"
									checked={shot.character_ids.includes(c.id)}
									onChange={(e) =>
										editShot(shot.id, {
											character_ids: e.target.checked
												? [...shot.character_ids, c.id]
												: shot.character_ids.filter((id) => id !== c.id),
										})
									}
								/>{' '}
								{c.name}
							</label>
						))}
					</div>
					<label className="block text-xs">
						Continuity reference
						<select
							className={FIELD}
							value={shot.previous_shot_id ?? ''}
							onChange={(e) =>
								editShot(shot.id, {
									previous_shot_id: e.target.value || null,
								})
							}
						>
							<option value="">Independent cut</option>
							{draft.shots
								.filter((s) => s.id !== shot.id)
								.map((source) => (
									<option key={source.id} value={source.id}>
										Ending of {source.title}
									</option>
								))}
						</select>
					</label>
					<div className="flex flex-wrap gap-2">
						{project.assets
							?.filter((a) => (a.kind ?? 'image') === 'image')
							.map((a) => (
								<label key={a.id} className="text-xs">
									<input
										type="checkbox"
										checked={shot.asset_ids.includes(a.id)}
										onChange={(e) =>
											editShot(shot.id, {
												asset_ids: e.target.checked
													? [...shot.asset_ids, a.id]
													: shot.asset_ids.filter((id) => id !== a.id),
											})
										}
									/>{' '}
									{a.name}
								</label>
							))}
					</div>
					<div className="flex items-center justify-between">
						<span className="text-xs text-muted-foreground">
							Seed {shot.seed} · {project.takes[shot.id]?.length ?? 0} takes
							retained
						</span>
						<Button
							size="sm"
							variant="outline"
							onClick={() => editShot(shot.id, { seed: shot.seed + 1 })}
						>
							Request new take
						</Button>
					</div>
				</fieldset>
				<details className="rounded-lg border border-border/60 p-3">
					<summary className="cursor-pointer text-xs text-muted-foreground">
						Edit decisions
						{index > 0 && edit.transition !== 'cut'
							? ` · ${edit.transition.replace('_', ' ')} in`
							: ''}
						{edit.out_seconds !== null || edit.in_seconds > 0
							? ` · ${edit.in_seconds}s–${edit.out_seconds ?? 'end'}`
							: ''}
						{edit.caption ? ' · caption' : ''}
					</summary>
					<fieldset disabled={busy} className="mt-2 grid gap-2 md:grid-cols-2">
						<label className="text-xs">
							In (s)
							<input
								type="number"
								min={0}
								step={0.1}
								className={FIELD}
								value={edit.in_seconds}
								onChange={(e) =>
									setEdit({ in_seconds: Number(e.target.value) || 0 })
								}
							/>
						</label>
						<label className="text-xs">
							Out (s, blank = end{seconds !== undefined ? ` ${seconds.toFixed(1)}` : ''})
							<input
								type="number"
								min={0}
								step={0.1}
								className={FIELD}
								value={edit.out_seconds ?? ''}
								onChange={(e) =>
									setEdit({ out_seconds: numberOrNull(e.target.value) })
								}
							/>
						</label>
						<label className="text-xs">
							Transition from previous shot
							<select
								className={FIELD}
								disabled={index === 0}
								value={edit.transition}
								onChange={(e) =>
									setEdit({ transition: e.target.value as Transition })
								}
							>
								<option value="cut">Cut</option>
								<option value="crossfade">Crossfade</option>
								<option value="fade_black">Dip to black</option>
							</select>
						</label>
						<label className="text-xs">
							Transition length (s)
							<input
								type="number"
								min={0.1}
								max={3}
								step={0.1}
								className={FIELD}
								disabled={index === 0 || edit.transition === 'cut'}
								value={edit.transition_seconds}
								onChange={(e) =>
									setEdit({
										transition_seconds: Number(e.target.value) || 0.5,
									})
								}
							/>
						</label>
						<label className="text-xs md:col-span-2">
							Caption (burned in)
							<input
								className={FIELD}
								maxLength={200}
								value={edit.caption}
								onChange={(e) => setEdit({ caption: e.target.value })}
							/>
						</label>
						<label className="text-xs">
							Caption from (s)
							<input
								type="number"
								min={0}
								step={0.1}
								className={FIELD}
								value={edit.caption_start}
								onChange={(e) =>
									setEdit({ caption_start: Number(e.target.value) || 0 })
								}
							/>
						</label>
						<label className="text-xs">
							Caption until (s, blank = shot end)
							<input
								type="number"
								min={0}
								step={0.1}
								className={FIELD}
								value={edit.caption_end ?? ''}
								onChange={(e) =>
									setEdit({ caption_end: numberOrNull(e.target.value) })
								}
							/>
						</label>
					</fieldset>
				</details>
				{job?.error && <p className="text-sm text-destructive">{job.error}</p>}
				{job?.status === 'completed' && (
					<div className="flex items-center gap-2">
						<Button
							size="sm"
							disabled={busy || dirty}
							variant={take?.review === 'accepted' ? 'default' : 'outline'}
							onClick={() =>
								void perform(() =>
									agentRequest(`/projects/${project.id}/review`, 'POST', {
										shot_id: shot.id,
										job_id: job.id,
										verdict: 'accepted',
									}),
								)
							}
						>
							Accept take
						</Button>
						<Button
							size="sm"
							variant="outline"
							disabled={busy || dirty}
							onClick={() =>
								void perform(() =>
									agentRequest(`/projects/${project.id}/review`, 'POST', {
										shot_id: shot.id,
										job_id: job.id,
										verdict: 'rejected',
									}),
								)
							}
						>
							Reject
						</Button>
						<span className="text-xs text-muted-foreground">
							{take?.review}
						</span>
					</div>
				)}
			</div>
		</article>
	);
}
