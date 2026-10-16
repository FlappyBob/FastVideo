'use client';
import { Button } from '@/components/ui/button';
import {
	agentRequest,
	assetUrl,
	uploadAgentAsset,
	type AgentConfig,
	type StoryBlueprint,
	type StoryProject,
} from '@/lib/agentApi';
const FIELD =
	'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-50';
export function CharacterLibrary({
	draft,
	project,
	busy,
	active,
	dirty,
	config,
	edit,
	perform,
}: {
	draft: StoryBlueprint;
	project: StoryProject;
	busy: boolean;
	active: boolean;
	dirty: boolean;
	config: AgentConfig | null;
	edit: (change: (value: StoryBlueprint) => void) => void;
	perform: (action: () => Promise<unknown>, refresh?: boolean) => Promise<void>;
}) {
	return (
		<section className="rounded-xl border border-border bg-card p-5">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<h2 className="font-semibold">Characters & reference library</h2>
				<div className="flex gap-2">
					<Button
						variant="outline"
						disabled={busy || active}
						onClick={() =>
							edit((v) => {
								v.characters.push({
									id: crypto.randomUUID(),
									name: 'New character',
									description: '',
									asset_ids: [],
								});
							})
						}
					>
						Add character
					</Button>
					<label className="cursor-pointer rounded-lg border px-3 py-2 text-sm">
						Upload reference
						<input
							aria-label="Upload reference"
							className="sr-only"
							type="file"
							accept="image/png,image/jpeg,image/webp"
							disabled={busy || dirty || active}
							onChange={(e) => {
								const file = e.target.files?.[0];
								if (file)
									void perform(() => uploadAgentAsset(project.id, file));
								e.target.value = '';
							}}
						/>
					</label>
				</div>
			</div>
			<div className="mt-4 flex flex-wrap gap-3">
				{project.assets?.filter((a) => (a.kind ?? 'image') === 'image').map((asset) => (
					<figure key={asset.id} className="w-24">
						<img
							src={assetUrl(project.id, asset.id)}
							alt={asset.name}
							className="h-20 w-24 rounded-md object-cover"
						/>
						<figcaption className="truncate text-xs text-muted-foreground">
							{asset.name}
						</figcaption>
					</figure>
				))}
			</div>
			{!project.assets?.length && (
				<p className="mt-3 text-sm text-muted-foreground">
					Reference images help anchor appearance across shots. Text
					descriptions alone do not lock identity.
				</p>
			)}
			<div className="mt-4 grid gap-3 md:grid-cols-2">
				{draft.characters.map((character) => (
					<fieldset
						key={character.id}
						disabled={busy || active}
						className="space-y-2 rounded-lg border p-3"
					>
						<label className="text-xs">
							Name
							<input
								className={FIELD}
								value={character.name}
								onChange={(e) =>
									edit((v) => {
										v.characters.find((c) => c.id === character.id)!.name =
											e.target.value;
									})
								}
							/>
						</label>
						<label className="block text-xs">
							Appearance and wardrobe
							<textarea
								className={FIELD}
								rows={2}
								value={character.description}
								onChange={(e) =>
									edit((v) => {
										v.characters.find(
											(c) => c.id === character.id,
										)!.description = e.target.value;
									})
								}
							/>
						</label>
						<div className="flex flex-wrap gap-2">
							{project.assets?.filter((a) => (a.kind ?? 'image') === 'image').map((asset) => (
								<label key={asset.id} className="text-xs">
									<input
										type="checkbox"
										checked={character.asset_ids.includes(asset.id)}
										onChange={(e) =>
											edit((v) => {
												const c = v.characters.find(
													(c) => c.id === character.id,
												)!;
												c.asset_ids = e.target.checked
													? [...c.asset_ids, asset.id]
													: c.asset_ids.filter((id) => id !== asset.id);
											})
										}
									/>{' '}
									{asset.name}
								</label>
							))}
						</div>
						<Button
							size="sm"
							variant="outline"
							disabled={
								busy || active || dirty || !config?.image_generator_configured
							}
							onClick={() =>
								void perform(() =>
									agentRequest(
										`/projects/${project.id}/references/generate`,
										'POST',
										{ revision: project.revision, character_id: character.id },
									),
								)
							}
						>
							Generate reference
						</Button>
					</fieldset>
				))}
			</div>
		</section>
	);
}
