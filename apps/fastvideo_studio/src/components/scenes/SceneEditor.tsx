'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import type { SceneInput } from '@/lib/api';
import type { Job } from '@/lib/types';

/** A draft is committed atomically so failed validation cannot partially reorder a scene. */
export function SceneEditor({ initial, jobs, onSave, onClose }: {
  initial: SceneInput; jobs: Job[]; onSave: (scene: SceneInput) => Promise<void>; onClose: () => void;
}) {
  const [draft, setDraft] = React.useState(initial);
  const [jobId, setJobId] = React.useState('');
  const [destination, setDestination] = React.useState('new');
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const used = new Set(draft.clips.flatMap((clip) => clip.job_ids));
  const available = jobs.filter((job) => !used.has(job.id));
  function move(index: number, offset: number) {
    const clips = [...draft.clips];
    [clips[index], clips[index + offset]] = [clips[index + offset], clips[index]];
    setDraft({...draft, clips});
  }
  function add() {
    const job = available.find((item) => item.id === jobId);
    if (!job) return;
    const clips = [...draft.clips];
    if (destination === 'new') clips.push({name: job.name || `Clip ${clips.length + 1}`, job_ids: [job.id], selected_job_id: job.id});
    else {
      const index = Number(destination);
      if (!clips[index]) return;
      clips[index] = {...clips[index], job_ids: [...clips[index].job_ids, job.id]};
    }
    setDraft({...draft, clips});
    setJobId('');
  }
  async function save() {
    setSaving(true); setError(null);
    try { await onSave(draft); onClose(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save scene'); }
    finally { setSaving(false); }
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}>
    <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>Edit scene</DialogTitle></DialogHeader>
      <fieldset disabled={saving} className="space-y-4">
        <label className="block text-sm">Scene name<Input value={draft.name} onChange={(e) => setDraft({...draft, name: e.target.value})}/></label>
        <ol className="space-y-3">
          {draft.clips.map((clip, index) => <li key={clip.id ?? clip.job_ids[0]} className="rounded border p-3 space-y-2">
            <label className="block text-sm">Clip {index + 1} name<Input value={clip.name} onChange={(e) => setDraft({...draft, clips: draft.clips.map((c, i) => i === index ? {...c, name: e.target.value} : c)})}/></label>
            <p className="text-xs text-muted-foreground">{clip.job_ids.length} take(s)</p>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={index === 0} onClick={() => move(index, -1)} aria-label={`Move clip ${index + 1} up`}>Up</Button>
              <Button size="sm" variant="outline" disabled={index === draft.clips.length - 1} onClick={() => move(index, 1)} aria-label={`Move clip ${index + 1} down`}>Down</Button>
              <Button size="sm" variant="outline" onClick={() => {setDraft({...draft, clips: draft.clips.filter((_, i) => i !== index)}); setDestination('new');}}>Remove clip {index + 1}</Button>
            </div>
          </li>)}
        </ol>
        <label className="block text-sm">Add job<NativeSelect aria-label="Add job" value={jobId} onChange={(e) => setJobId(e.target.value)}>
          <option value="">Choose an existing inference job</option>
          {available.map((job) => <option key={job.id} value={job.id}>{job.name || job.id} · {job.status}</option>)}
        </NativeSelect></label>
        <label className="block text-sm">Add as<NativeSelect aria-label="Add as" value={destination} onChange={(e) => setDestination(e.target.value)}>
          <option value="new">New clip</option>
          {draft.clips.map((clip, index) => <option key={index} value={index}>Take of clip {index + 1}: {clip.name}</option>)}
        </NativeSelect></label>
        <Button variant="outline" disabled={!jobId} onClick={add}>Add to scene</Button>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!draft.name.trim() || saving} onClick={save}>{saving ? 'Saving…' : 'Save scene'}</Button>
        </div>
      </fieldset>
    </DialogContent>
  </Dialog>;
}
