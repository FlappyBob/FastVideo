import { expect, test } from '@playwright/test';
import { API_BASE, skipWithoutMock } from './helpers';

skipWithoutMock();
test('story references, approval, review, export and dependent revision survive refresh', async ({page,request}) => {
  const failures: string[]=[];
  page.on('pageerror',error=>failures.push(error.message));
  await page.goto('/agent');
  await expect(page.getByText(/Simulated generation/)).toBeVisible();
  await page.getByRole('button',{name:'New story',exact:true}).click();
  await expect(page.getByLabel('Story title')).toHaveValue('Untitled story');
  const projects=await (await request.get(`${API_BASE}/agent/projects`)).json();
  const id=projects[0].id;
  // Actual media upload; this tiny PNG is a synthetic test reference, not a generated portrait.
  const assetResponse=await request.post(`${API_BASE}/agent/projects/${id}/assets`,{multipart:{file:{name:'red-reference.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU1sAAAAASUVORK5CYII=','base64')}}});
  expect(assetResponse.ok()).toBeTruthy();
  const asset=await assetResponse.json();
  const blueprint={title:`Continuity test ${Date.now()}`,brief:'A robot crosses a station',style:'blue and amber',
    recipe:{model_id:'MiniMaxAI/MiniMax-H3',width:256,height:128,num_frames:16,fps:8,num_inference_steps:1},
    characters:[{id:'robot',name:'Robot',description:'small yellow robot',asset_ids:[asset.id]}],
    shots:[0,1,2].map(i=>({id:`shot${i}`,title:`Shot ${i+1}`,prompt:`A robot walks through station ${i}`,character_ids:['robot'],asset_ids:[],previous_shot_id:i?`shot${i-1}`:null,seed:1024+i}))};
  await page.getByLabel('Import storyboard').setInputFiles({name:'story.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(blueprint))});
  await expect(page.getByLabel('Story title')).toHaveValue(blueprint.title);
  await page.getByRole('button',{name:'Approve storyboard'}).click();
  await expect(page.getByRole('button',{name:'Generate missing shots'})).toBeEnabled();
  await page.getByRole('button',{name:'Generate missing shots'}).click();
  await expect(page.getByRole('button',{name:'Accept take'})).toHaveCount(3,{timeout:90_000});
  const before=await (await request.get(`${API_BASE}/agent/projects/${id}`)).json();
  expect(Object.keys(before.selected)).toHaveLength(3);
  expect(before.jobs[before.selected.shot1].references).toContainEqual({source:`job-last-frame:${before.selected.shot0}`,media_type:'image'});
  for(let i=0;i<3;i++){
    await page.getByRole('button',{name:'Accept take'}).nth(i).click();
    await expect(page.getByRole('button',{name:'Accept take'}).nth(i)).toBeEnabled();
  }
  await expect(page.getByRole('button',{name:'Export accepted shots'})).toBeEnabled();
  await page.getByRole('button',{name:'Export accepted shots'}).click();
  await expect(page.getByRole('link',{name:'Download film'})).toBeVisible();
  const url=await page.getByRole('link',{name:'Download film'}).getAttribute('href');
  expect((await request.get(new URL(url!,page.url()).href)).ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByLabel('Story title')).toHaveValue(blueprint.title);
  await page.getByLabel('Action, framing, camera and sound').nth(1).fill('The same robot looks up at the station clock');
  await page.getByRole('button',{name:'Save storyboard'}).click();
  await expect(page.getByRole('button',{name:'Approve storyboard'})).toBeEnabled();
  await page.getByRole('button',{name:'Approve storyboard'}).click();
  await page.getByLabel('New-job budget').fill('2');
  await expect(page.getByRole('button',{name:'Generate missing shots'})).toBeEnabled();
  await page.getByRole('button',{name:'Generate missing shots'}).click();
  await expect(page.getByRole('button',{name:'Accept take'})).toHaveCount(3,{timeout:90_000});
  const after=await (await request.get(`${API_BASE}/agent/projects/${id}`)).json();
  expect(after.selected.shot0).toBe(before.selected.shot0);
  expect(after.selected.shot1).not.toBe(before.selected.shot1);
  expect(after.selected.shot2).not.toBe(before.selected.shot2);
  expect(after.takes.shot1).toHaveLength(2);
  expect(failures).toEqual([]);
  await page.screenshot({path:'../../outputs/studio_video_agent/agent-workspace.png',fullPage:true});
});
