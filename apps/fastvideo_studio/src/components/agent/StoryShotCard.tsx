'use client';
import { Button } from '@/components/ui/button';
import {
	agentRequest,
	type StoryBlueprint,
	type StoryProject,
	type StoryShot,
} from '@/lib/agentApi';
const FIELD =
	'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-50';
import { getJobVideoUrl } from '@/lib/api';
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
						{project.assets?.map((a) => (
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
