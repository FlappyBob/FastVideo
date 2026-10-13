'use client';

import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import * as React from 'react';

import CreateJobModal from '@/components/jobs/CreateJobModal';
import { EDITABLE_STATUSES, SceneBoard } from '@/components/scenes/SceneBoard';
import { MergedSceneDialog } from '@/components/scenes/MergedSceneDialog';
import { SceneEditor } from '@/components/scenes/SceneEditor';
import { SceneQueueDialog } from '@/components/scenes/SceneQueueDialog';
import { TrimClipDialog } from '@/components/scenes/TrimClipDialog';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { NativeSelect } from '@/components/ui/native-select';
import { toast } from 'sonner';
import { createScene, getScenes, importLegacyScenes, updateScene, dequeueJob, getJobsList, mergeScene, queueJobs, updateJob, type MergedScene, type StoredScene, type SceneInput } from '@/lib/api';
import { attachedLastFrames, attachLastFrame, deferredLastFrameSource, detachLastFrame } from '@/lib/continuation';
import type { SceneQueuePlan } from '@/lib/sceneQueue';
import { buildScenes, buildStoredScenes, sceneInput, type Scene, type SceneClip } from '@/lib/scenes';
import type { Job } from '@/lib/types';

const POLL_MS = 3000;

export default function ScenesPage() {
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadKey, setReloadKey] = React.useState(0);
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);
  const [openJob, setOpenJob] = React.useState<Job | null>(null);
  const [records, setRecords] = React.useState<StoredScene[]>([]);
  const [editingScene, setEditingScene] = React.useState<{id?: string; input: SceneInput} | null>(null);
  const [savingScene, setSavingScene] = React.useState(false);
  const [queueTarget, setQueueTarget] = React.useState<Scene | null>(null);
  const [merged, setMerged] = React.useState<(MergedScene & { title: string }) | null>(null);
  const [trimTarget, setTrimTarget] = React.useState<Job | null>(null);
  // Bumped per job id whenever its video is edited in place, to cache-bust every
  // <video> for that job (the dialog's own preview and the scene board's). Owned
  // here, not by the dialog, so it survives the dialog re-rendering with a fresh
  // job object after each edit -- a per-dialog counter got reset by that same
  // re-render before the browser ever saw the new URL.
  const [videoVersions, setVideoVersions] = React.useState<Record<string, number>>({});
  const bumpVideoVersion = (jobId: string) =>
    setVideoVersions((v) => ({ ...v, [jobId]: (v[jobId] ?? 0) + 1 }));

  React.useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [list, stored] = await Promise.all([getJobsList('inference'), getScenes()]);
        if (cancelled) return;
        setJobs(list);
        setRecords(stored);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load jobs');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  function reload() {
    setIsLoading(true);
    setReloadKey((k) => k + 1);
  }

  // While anything is queued or running, keep the board current without the
  // loading state (which would blank or flicker it).
  const hasActive = jobs.some((j) => j.status === 'queued' || j.status === 'running');
  React.useEffect(() => {
    if (!hasActive) return;
    let cancelled = false;
    let refreshing = false;
    const interval = setInterval(() => {
      if (refreshing) return;
      refreshing = true;
      Promise.all([getJobsList('inference'), getScenes()])
        .then(([list, stored]) => {
          if (cancelled) return;
          setJobs(list);
          setRecords(stored);
        })
        .catch(() => {})
        .finally(() => { refreshing = false; });
    }, POLL_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, [hasActive]);

  // Attach the previous clip's last frame to `clip` and rewrite its prompt to open on
  // it. The frame is a reference the server resolves when the clip runs, so the
  // previous clip doesn't have to be finished yet. Returns the picture label.
  async function attachPreviousFrame(clip: SceneClip, previous: SceneClip): Promise<string> {
    const result = attachLastFrame(
      clip.job.prompt,
      clip.job.references ?? [],
      deferredLastFrameSource(previous.job.id),
      clip.job.model_id,
    );
    if (!result.ok) throw new Error(result.error);
    await updateJob(clip.job.id, { references: result.references, prompt: result.prompt });
    return result.label;
  }

  async function useLastFrame(clip: SceneClip, previous: SceneClip) {
    try {
      const label = await attachPreviousFrame(clip, previous);
      toast.success(`Clip ${clip.position} now starts from the last frame of clip ${previous.position} (${label}).`);
      reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't use the last frame.");
    }
  }

  // Undo the above: take every attached last frame back off the clip.
  async function stopUsingLastFrame(clip: SceneClip) {
    try {
      let prompt = clip.job.prompt;
      let references = clip.job.references ?? [];
      // Newest first: only the most recently added image can be removed cleanly.
      for (const { source } of attachedLastFrames(references).reverse()) {
        const result = detachLastFrame(prompt, references, source);
        if (!result.ok) throw new Error(result.error);
        prompt = result.prompt;
        references = result.references;
      }
      await updateJob(clip.job.id, { references, prompt });
      toast.success(`Clip ${clip.position} no longer starts from a previous clip's last frame.`);
      reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't remove the last frame.");
    }
  }

  // Join the scene's clips, in the order shown (the take chosen for each), into one video.
  async function mergeSceneClips(scene: Scene) {
    try {
      if (scene.missingJobs) throw new Error('Review deleted jobs in Edit scene before merging.');
      const result = await mergeScene(scene.clips.map((c) => c.job.id), scene.title);
      setMerged({ ...result, title: scene.title });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't merge the scene.");
    }
  }

  async function queueClip(clip: SceneClip) {
    try {
      await queueJobs([clip.job.id]);
      toast.success(`Queued clip ${clip.position}.`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't queue the clip.");
    }
    reload();
  }

  async function dequeueClip(clip: SceneClip) {
    try {
      await dequeueJob(clip.job.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't remove the clip from the queue.");
    }
    reload();
  }

  // Link clips to the frames before them (if asked), then queue them all in scene order.
  async function queueScene(plan: SceneQueuePlan) {
    try {
      for (const { clip, previous } of plan.chain) {
        await attachPreviousFrame(clip, previous).catch((e) => {
          throw new Error(`Clip ${clip.position}: ${e instanceof Error ? e.message : e}. Nothing was queued.`);
        });
      }
      await queueJobs(plan.queue.map((c) => c.job.id));
      const n = plan.queue.length;
      toast.success(`Queued ${n} clip${n === 1 ? '' : 's'}.`);
      setQueueTarget(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't queue the scene.");
    }
    reload();
  }

  const scenes = React.useMemo(() => {
    const associated = new Set(records.flatMap((record) => record.clips.flatMap((clip) => clip.job_ids)));
    return [...buildStoredScenes(records, jobs), ...buildScenes(jobs.filter((job) => !associated.has(job.id)))];
  }, [jobs, records]);
  const scene = scenes.find((s) => s.key === selectedKey) ?? scenes[0];

  async function saveScene(input: SceneInput, id?: string) {
    const saved = id ? await updateScene(id, input) : await createScene(input);
    setRecords((previous) => [saved, ...previous.filter((item) => item.id !== saved.id)]);
    setSelectedKey(saved.id);
  }

  async function chooseTake(takeKey: string, jobId: string) {
    if (!scene || savingScene) return;
    setSavingScene(true);
    try {
      const record = records.find((item) => item.id === scene.key);
      const input = record ?? sceneInput(scene);
      const index = scene.clips.findIndex((clip) => clip.takeKey === takeKey);
      await saveScene({...input, clips: input.clips.map((clip, i) =>
        (record ? clip.id === takeKey : i === index) ? {...clip, selected_job_id: jobId} : clip)}, record?.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save the selected take');
    } finally { setSavingScene(false); }
  }

  async function importScenes() {
    setSavingScene(true);
    try { setRecords(await importLegacyScenes()); }
    catch (e) { toast.error(e instanceof Error ? e.message : 'Could not import scenes'); }
    finally { setSavingScene(false); }
  }

  return (
    <div className="mx-auto w-full max-w-[1200px] px-4 pb-12 pt-4">
      <Card className="p-6">
        <div className="mb-1 flex flex-wrap items-center gap-3">
          <h2 className="text-2xl font-semibold text-foreground">Scenes</h2>
          <Button size="sm" variant="outline" onClick={() => setEditingScene({input: {name: 'New scene', clips: []}})}>New scene</Button>
          <Button size="sm" variant="outline" disabled={savingScene || isLoading} onClick={importScenes}>Import job groups</Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="ml-auto gap-1.5"
            onClick={reload}
            disabled={isLoading}
          >
            <RefreshCw className={isLoading ? 'size-3.5 animate-spin' : 'size-3.5'} aria-hidden />
            Refresh
          </Button>
        </div>
        <p className="mb-6 text-sm text-muted-foreground">
          Save scenes to keep clip order, names, and selected takes across reloads. Unassigned jobs are previewed by name, like{' '}
          <code className="font-mono text-xs">wolf-lunch-clip-01</code>,{' '}
          <code className="font-mono text-xs">wolf-lunch-clip-02</code>, and can be saved with Import job groups.
        </p>

        {isLoading && jobs.length === 0 ? (
          <div className="flex items-center gap-3 p-8 text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
            <span>Loading scenes…</span>
          </div>
        ) : error && jobs.length === 0 ? (
          <div role="alert" className="flex flex-col items-center gap-3 py-8 text-center">
            <AlertTriangle className="size-6 text-destructive" aria-hidden />
            <p className="max-w-md text-sm text-muted-foreground">{error}</p>
            <Button type="button" variant="outline" onClick={reload}>
              Try Again
            </Button>
          </div>
        ) : !scene ? (
          <p className="py-8 text-center text-muted-foreground">
            No inference jobs yet. Create some and they&apos;ll show up here.
          </p>
        ) : (
          <div className="flex flex-col gap-6">
            {scenes.length > 1 && (
              <label className="flex max-w-md flex-col gap-1 text-xs text-muted-foreground">
                Scene
                <NativeSelect value={scene.key} onChange={(e) => setSelectedKey(e.target.value)}>
                  {scenes.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.title} ({s.clips.length} clip{s.clips.length === 1 ? '' : 's'})
                    </option>
                  ))}
                </NativeSelect>
              </label>
            )}
            <div className="flex items-center gap-3">
              <Button size="sm" variant="outline" onClick={() => setEditingScene({
                id: scene.persisted ? scene.key : undefined,
                input: records.find((item) => item.id === scene.key) ?? sceneInput(scene),
              })}>{scene.persisted ? 'Edit scene' : 'Save / edit scene'}</Button>
              <span className="text-xs text-muted-foreground">{savingScene ? 'Saving…' : scene.persisted ? 'Saved scene' : 'Unsaved job group'}</span>
            </div>
            {!!scene.missingJobs && <p role="alert" className="text-sm text-destructive">{scene.missingJobs} referenced job(s) were deleted. Review this scene before merging.</p>}
            <SceneBoard
              scene={scene}
              onOpenJob={setOpenJob}
              onUseLastFrame={useLastFrame}
              onDetachLastFrame={stopUsingLastFrame}
              onQueueScene={setQueueTarget}
              onMergeScene={mergeSceneClips}
              onQueueClip={queueClip}
              onDequeueClip={dequeueClip}
              onTrimClip={(clip) => setTrimTarget(clip.job)}
              onChooseTake={chooseTake}
              videoVersionFor={(jobId) => videoVersions[jobId] ?? 0}
            />
          </div>
        )}
      </Card>

      {editingScene && <SceneEditor initial={editingScene.input} jobs={jobs}
        onSave={(input) => saveScene(input, editingScene.id)} onClose={() => setEditingScene(null)} />}
      <MergedSceneDialog merged={merged} onClose={() => setMerged(null)} />
      <SceneQueueDialog scene={queueTarget} onConfirm={queueScene} onClose={() => setQueueTarget(null)} />
      <TrimClipDialog
        clip={trimTarget}
        videoVersion={trimTarget ? (videoVersions[trimTarget.id] ?? 0) : 0}
        onChanged={(job) => {
          setTrimTarget(job);
          bumpVideoVersion(job.id);
          getJobsList('inference').then(setJobs).catch(() => {});
        }}
        onClose={() => setTrimTarget(null)}
      />

      {openJob && (
        <CreateJobModal
          isOpen
          readOnly={!EDITABLE_STATUSES.includes(openJob.status)}
          editingJob={openJob}
          jobType={(openJob.job_type ?? 'inference') as never}
          workloadType={openJob.workload_type ?? 't2v'}
          onClose={() => setOpenJob(null)}
          onSuccess={() => {
            setOpenJob(null);
            reload();
          }}
        />
      )}
    </div>
  );
}
