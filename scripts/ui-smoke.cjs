// NODE_PATH=/path/to/playwright/node_modules node scripts/ui-smoke.cjs
// Both servers use temporary data directories. No production meetings are created.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const { mkdtempSync, mkdirSync, readFileSync, realpathSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname,'..');
const artifacts = process.env.UI_ARTIFACTS || path.join(root,'artifacts');
mkdirSync(artifacts,{recursive:true});
const children=[];
async function server(module, port, provider) {
  const child=spawn(path.join(realpathSync(path.join(root,'.venv')),'bin/python'),['-m','uvicorn',module,'--host','127.0.0.1','--port',String(port)],{
    cwd:root,env:{...process.env,PROVIDER:provider,DATA_DIR:mkdtempSync(path.join(tmpdir(),'tingjian-ui-')),ROOT_PATH:''},stdio:['ignore','pipe','pipe']});
  children.push(child);
  let log='';child.stdout.on('data',d=>log+=d);child.stderr.on('data',d=>log+=d);
  for(let i=0;i<150;i++){
    try{const response=await fetch(`http://127.0.0.1:${port}/api/health`);if(response.ok)return;}
    catch{}
    if(child.exitCode!==null)throw new Error(log);
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  throw new Error(`Server readiness timeout: ${log}`);
}
function wav(){
  const data=Buffer.alloc(32000);for(let i=0;i<16000;i++)data.writeInt16LE(Math.sin(i/8)*2000,i*2);
  const head=Buffer.alloc(44);head.write('RIFF');head.writeUInt32LE(36+data.length,4);head.write('WAVEfmt ',8);
  head.writeUInt32LE(16,16);head.writeUInt16LE(1,20);head.writeUInt16LE(1,22);head.writeUInt32LE(16000,24);
  head.writeUInt32LE(32000,28);head.writeUInt16LE(2,32);head.writeUInt16LE(16,34);head.write('data',36);head.writeUInt32LE(data.length,40);
  return Buffer.concat([head,data]);
}
(async()=>{
  let browser;
  const errors=[];
  try{
    await server('app.main:app',8776,'demo');
    await server('tests.audio_server:app',8777,'qwen');
    browser=await chromium.launch({headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8776');
    await page.waitForSelector('#mode-badge:text-is("演示模式")');
    await page.screenshot({path:path.join(artifacts,'desktop-empty.png'),fullPage:true});
    await page.click('#demo-button');
    await page.waitForFunction(()=>document.querySelectorAll('.segment').length===8,{},{timeout:25000});
    await page.waitForFunction(()=>document.querySelectorAll('.note-card').length===2);
    await page.waitForFunction(()=>document.querySelector('#demo-button').textContent.includes('播放示例'));
    assert.equal(await page.locator('.segment-translation').count(),8);
    await page.screenshot({path:path.join(artifacts,'desktop-demo.png'),fullPage:true});
    await page.click('#original-tab');
    assert.equal(await page.locator('.segment-translation').first().isVisible(),false);
    await page.click('#bilingual-tab');
    await page.click('#search-button');await page.fill('#transcript-search','四句话');
    assert.equal(await page.locator('.segment').count(),1);
    await page.fill('#transcript-search','');
    await page.locator('[data-source]').first().click();
    assert.equal(await page.locator('.segment.highlight').count(),1);
    await page.click('#export-button');
    const downloadPromise=page.waitForEvent('download');await page.click('[data-export="md"]');
    const download=await downloadPromise;const exported=path.join(artifacts,'meeting.md');await download.saveAs(exported);
    assert.match(readFileSync(exported,'utf8'),/分段纪要/);
    await page.click('#finish-button');await page.waitForSelector('#session-status:text-is("已完成")');
    await page.reload();await page.waitForSelector('#session-status:text-is("已完成")');
    assert.equal(await page.locator('.segment').count(),8);
    await page.click('#new-meeting');
    assert.equal(await page.inputValue('#new-title'),'新会议');
    assert.equal(await page.inputValue('#new-form [name=source]'),'en');
    assert.equal(await page.inputValue('#new-form [name=target]'),'zh');
    assert.equal(await page.inputValue('#new-form [name=glossary]'),'');
    await page.fill('#new-title','新的课堂');await page.click('#new-form [type=submit]');
    await page.waitForSelector('#meeting-title:text-is("新的课堂")');
    assert.equal(await page.locator('.segment').count(),0);assert.equal(await page.locator('.note-card').count(),0);
    await page.fill('#text-input','<img src=x onerror=alert(1)>');await page.click('#text-form button');
    await page.waitForFunction(()=>document.querySelector('.segment-translation')?.textContent.includes('未调用翻译模型'));
    assert.equal(await page.locator('.segment img').count(),0);
    assert.match(await page.locator('.segment-text').textContent(),/<img/);
    await page.fill('#text-input','这句中文保留原文。');await page.click('#text-form button');
    await page.waitForFunction(()=>document.querySelectorAll('.segment').length===2);
    const chinese=page.locator('.segment').filter({hasText:'这句中文保留原文。'});
    await page.waitForFunction(()=>{
      const row=[...document.querySelectorAll('.segment')].find(s=>s.textContent.includes('这句中文保留原文。'));
      return row&&!row.querySelector('.segment-translation');
    });
    assert.equal(await chinese.locator('.segment-translation').count(),0);
    await page.setViewportSize({width:390,height:844});await page.waitForTimeout(350);await page.screenshot({path:path.join(artifacts,'mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile horizontal overflow');
    const context=await browser.newContext({permissions:['microphone'],viewport:{width:1366,height:900}});
    await context.addInitScript(()=>{
      navigator.mediaDevices.getUserMedia=async()=>{
        const ctx=new AudioContext();await ctx.resume();
        const oscillator=ctx.createOscillator(),gain=ctx.createGain(),destination=ctx.createMediaStreamDestination();
        oscillator.frequency.value=220;
        gain.gain.setValueAtTime(.05,ctx.currentTime);
        gain.gain.setValueAtTime(0,ctx.currentTime+3);
        gain.gain.setValueAtTime(.05,ctx.currentTime+4.2);
        oscillator.connect(gain);gain.connect(destination);oscillator.start();
        return destination.stream;
      };
    });
    const audio=await context.newPage();audio.on('pageerror',e=>errors.push(e.message));
    await audio.goto('http://127.0.0.1:8777');await audio.waitForSelector('#mode-badge:text-is("本地模型")');
    await audio.click('#mic-button');await audio.waitForSelector('#recording-title:text-is("录音中")');
    await audio.waitForFunction(()=>document.querySelector('.draft .segment-text')?.textContent.includes('第一句话'),{},{timeout:10000});
    assert.equal(await audio.locator('#recording-title').textContent(),'录音中','draft must appear during capture');
    await audio.screenshot({path:path.join(artifacts,'desktop-live.png'),fullPage:true});
    await audio.waitForFunction(()=>document.querySelectorAll('.segment[data-id]').length===2,{},{timeout:10000});
    assert.equal(await audio.locator('#recording-title').textContent(),'录音中','sentence finals must arrive without stopping capture');
    assert.equal(await audio.locator('.segment[data-id] .segment-text').first().textContent(),'第一句话已经识别。');
    await audio.waitForFunction(()=>document.querySelector('.draft .segment-text')?.textContent.includes('第一句话'),{},{timeout:10000});
    await audio.click('#mic-button');await audio.waitForSelector('#recording-title:text-is("未录音")');
    await audio.waitForTimeout(500);
    const mid=await audio.evaluate(()=>localStorage.getItem('tingjian.lastMeeting'));
    let meeting=await (await fetch(`http://127.0.0.1:8777/api/meetings/${mid}`)).json();
    assert.ok(meeting.segments.some(s=>s.audio_path),'microphone PCM must persist');
    await audio.locator('[data-audio]').first().click();assert.equal(await audio.locator('audio').count(),1);
    await audio.locator('#audio-file').setInputFiles({name:'upload.wav',mimeType:'audio/wav',buffer:wav()});
    await audio.waitForTimeout(1000);await audio.click('#finish-button');await audio.waitForSelector('#session-status:text-is("已完成")');
    meeting=await (await fetch(`http://127.0.0.1:8777/api/meetings/${mid}`)).json();
    assert.ok(meeting.segments.length>=2,'microphone plus WAV upload');
    assert.ok(meeting.summaries.length>=1);
    await audio.click('#retranslate-button');
    await audio.waitForSelector('.toast:text-is("重译任务已完成")');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',checks:['demo pipeline','automatic minutes','source navigation','search','original-only view','Markdown download','reload persistence','new meeting empty state','XSS escaping','390px responsive layout','rolling subtitle before stop (test provider)','sentence finals while recording','microphone AudioWorklet + VAD + PCM transport (test provider)','audio playback','WAV upload','final drain','retranslation'],artifacts},null,2));
  }finally{
    if(browser)await browser.close();
    for(const child of children)child.kill('SIGTERM');
    await Promise.all(children.map(child=>new Promise(resolve=>{if(child.exitCode!==null)resolve();else{child.once('exit',resolve);setTimeout(()=>{child.kill('SIGKILL');resolve();},3000).unref();}})));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
