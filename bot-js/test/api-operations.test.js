import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { JsonStore } from '../src/store.js';
import { createApi, authorised } from '../src/api.js';
import { ProfileService, profileInput, avatarURL, publicIPv4 } from '../src/profile.js';
import { BackupService, caseRows } from '../src/modules/operations.js';
import { csvFile } from '../src/helpers.js';

async function temporary(t) {
  const dir = await mkdtemp(join(tmpdir(),'rallybit-api-'));
  t.after(()=>rm(dir,{recursive:true,force:true})); return new JsonStore(dir);
}
test('API authentication rejects missing, empty, wrong and different-length secrets',()=>{
  for (const [a,b] of [['',''],['','secret'],['wrong','secret'],['secre','secret']]) assert.equal(authorised(a,b),false);
  assert.equal(authorised('secret','secret'),true);
});
test('dashboard JSON bridge supports legacy arrays, exact IDs, auth and safe filenames',async t=>{
  const store=await temporary(t),client={isReady:()=>true,guilds:{cache:new Map()},ws:{ping:20}};
  const api=createApi({store,client,secret:'test-key',topggSecret:'vote-key'});
  api.listen(0,'127.0.0.1'); await once(api,'listening'); t.after(()=>new Promise(resolve=>api.close(resolve)));
  const request=async(path,body,key='test-key',header='X-Api-Key')=>{
    const response=await fetch(`http://127.0.0.1:${api.address().port}${path}`,{method:'POST',headers:{[header]:key,'Content-Type':'application/json'},body:typeof body==='string'?body:JSON.stringify(body)});
    return {status:response.status,body:await response.json()};
  };
  assert.equal((await request('/api/json/read',{file:'notice.json'},'wrong')).status,401);
  assert.equal((await request('/api/json/read',{file:'../.env'})).status,400);
  assert.equal((await request('/api/json/write',{file:'admins.json',data:['123456789012345678']})).status,200);
  assert.deepEqual((await request('/api/json/read',{file:'admins.json'})).body.data,['123456789012345678']);
  const raw='{"file":"notice.json","data":{"role_id":1515880619656220683}}';
  assert.equal((await request('/api/json/write',raw)).status,200);
  assert.equal((await request('/api/json/read',{file:'notice.json'})).body.data.role_id,'1515880619656220683');
  assert.equal((await request('/api/premium/grant',{})).status,410);
  assert.equal((await request('/api/topgg/webhook',{user:'123456789012345678'},'vote-key','Authorization')).status,200);
  assert.equal((await store.read('votes.json',[]))[0].user_id,'123456789012345678');
});
test('dashboard API returns failure when storage cannot save instead of claiming success',async t=>{
  const api=createApi({store:{write:async()=>{throw Object.assign(new Error('full'),{code:'ENOSPC'});}},client:{},secret:'test-key'});
  api.listen(0,'127.0.0.1');await once(api,'listening');t.after(()=>new Promise(resolve=>api.close(resolve)));
  const response=await fetch(`http://127.0.0.1:${api.address().port}/api/json/write`,{method:'POST',headers:{'X-Api-Key':'test-key'},body:JSON.stringify({file:'security_settings.json',data:{}})});
  assert.equal(response.status,507);assert.ok((await response.json()).error.includes('could not be saved'));
});
test('shared profile inputs validate all presence options, names and URLs',()=>{
  for (const status of ['online','idle','dnd','offline']) assert.equal(profileInput({name:'Rallybit',status}).status,status);
  for (const input of [{status:'afk'},{name:'x'},{name:'x'.repeat(33)},{avatar_url:'http://example.com/a.png'},{avatar_url:'https://user:pass@example.com/a.png'}]) assert.throws(()=>profileInput(input));
  assert.throws(()=>avatarURL('file:///etc/passwd'));
  assert.equal(publicIPv4('8.8.8.8'),true);
  for (const address of ['127.0.0.1','10.0.0.1','192.168.0.71','172.18.0.1','169.254.169.254','100.64.0.1','0.0.0.0','::1','203.0.113.1']) assert.equal(publicIPv4(address),false);
});
test('rejected Discord profile updates roll saved settings back',async t=>{
  const store=await temporary(t);const previous={global:{profile_name:'Original',presence_status:'idle'}};await store.write('bot_settings.json',previous);
  let presence;
  const client={presence:{status:'idle'},user:{username:'Original',avatar:null,edit:async()=>{throw new Error('Discord rejected');},setPresence:value=>{presence=value;}}};
  await assert.rejects(new ProfileService(store,client).update({name:'Replacement',status:'dnd'}),/Saved settings were restored/);
  assert.deepEqual(await store.read('bot_settings.json'),previous);assert.equal(presence.status,'idle');
});
test('profile changes persist and offline maps to Discord invisible',async t=>{
  const store=await temporary(t);const client={presence:{status:'online'},user:{username:'Rallybit',avatar:null,edit:async()=>{},setPresence:value=>{client.presence=value;}}};
  await new ProfileService(store,client).update({name:'Rallybit',status:'offline'});
  assert.equal((await store.read('bot_settings.json')).global.presence_status,'offline');assert.equal(client.presence.status,'invisible');
});
test('backups restore only their own server and do not delete newer absent sections',async t=>{
  const store=await temporary(t);await store.write('welcome_settings.json',{one:{welcome_enabled:true},two:{welcome_enabled:true}});
  await store.write('audit_settings.json',{one:{enabled:true}});
  const backup=new BackupService(store);
  await backup.restore('one',{id:'OLD',settings:{welcomes:{welcome_enabled:false}}});
  const welcomes=await store.read('welcome_settings.json');assert.equal(welcomes.one.welcome_enabled,false);assert.equal(welcomes.two.welcome_enabled,true);
  assert.equal((await store.read('audit_settings.json')).one.enabled,true);
});
test('unfinished backup restores recover before runtime startup',async t=>{
  const store=await temporary(t);await store.write('welcome_settings.json',{one:{welcome_enabled:false},two:{welcome_enabled:true}});
  await store.write('backup_restore_journal.json',{status:'pending',guild_id:'one',before:{welcomes:{welcome_enabled:true}}});
  await new BackupService(store).recover();
  assert.equal((await store.read('welcome_settings.json')).one.welcome_enabled,true);assert.equal((await store.read('welcome_settings.json')).two.welcome_enabled,true);
  assert.equal((await store.read('backup_restore_journal.json')).status,'rolled_back');
});
test('case filters understand both Python action labels and JavaScript action names',async t=>{
  const store=await temporary(t);await store.write('moderation_history.json',{guild:{target:[{action:'Warning added',timestamp:new Date().toISOString(),moderator_id:'mod'},{action:'warn',timestamp:new Date().toISOString(),moderator_id:'other'}]}});
  assert.equal((await caseRows(store,'guild',{action:'warn'})).length,2);
  assert.equal((await caseRows(store,'guild',{action:'warn',moderator:'mod'})).length,1);
});
test('CSV exports escape spreadsheet formulas and quote embedded separators',()=>{
  const file=csvFile('test.csv',['Reason'],[['=SUM(1,2)'],['ordinary, "quoted" text']]);
  const contents=file.attachment.toString();assert.ok(contents.includes("'=SUM(1,2)"));assert.ok(contents.includes('"ordinary, ""quoted"" text"'));
});
