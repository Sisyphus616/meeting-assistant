// Real local-model check with a WAV fixture fed through browser microphone APIs.
// NODE_PATH=/path/to/playwright/node_modules node scripts/live-browser-smoke.cjs sample.wav
// Creates a labelled test meeting. Does not access a physical microphone.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const origin = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8765';
const sample = process.argv[2];
if (!sample) throw new Error('Usage: node scripts/live-browser-smoke.cjs sample.wav');
const artifacts = process.env.UI_ARTIFACTS || path.resolve(__dirname,'../artifacts');
fs.mkdirSync(artifacts,{recursive:true});
(async()=>{
  const browser = await chromium.launch({headless:true,args:['--use-fake-ui-for-media-stream']});
  const errors = [];
  try {
    const context = await browser.newContext({permissions:['microphone'],viewport:{width:1400,height:1000}});
    await context.route('**/__sample.wav',route=>route.fulfill({status:200,contentType:'audio/wav',body:fs.readFileSync(sample)}));
    await context.addInitScript(()=>{
      navigator.mediaDevices.getUserMedia=async()=>{
        const ctx=new AudioContext();await ctx.resume();
        const response=await fetch('/__sample.wav');
        const buffer=await ctx.decodeAudioData(await response.arrayBuffer());
        const source=ctx.createBufferSource(),gain=ctx.createGain(),output=ctx.createMediaStreamDestination();
        source.buffer=buffer;source.loop=true;source.connect(gain);gain.connect(output);
        const timer=setInterval(()=>{
          if(document.querySelector('#recording-title')?.textContent!=='录音中')return;
          clearInterval(timer);ctx.resume();
          gain.gain.setValueAtTime(1,ctx.currentTime);gain.gain.setValueAtTime(0,ctx.currentTime+10);
          source.start();
        },20);
        return output.stream;
      };
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin);await page.waitForSelector('#mode-badge:text-is("本地模型")');
    await page.click('#new-meeting');await page.fill('#new-title','本地实时转写验证（公开样本）');
    await page.click('#new-form [type=submit]');await page.waitForSelector('#session-status:text-is("记录中")');
    const prepareStarted=Date.now();await page.click('#mic-button');
    await page.waitForSelector('#recording-title:text-is("录音中")',{timeout:180000});
    const started=Date.now();
    await page.waitForFunction(()=>{
      const text=document.querySelector('.draft .segment-text')?.textContent;
      return text&&text!=='正在识别…';
    },{},{timeout:60000});
    const firstSubtitleMs=Date.now()-started;
    const firstText=await page.locator('.draft .segment-text').first().textContent();
    assert.equal(await page.locator('#recording-title').textContent(),'录音中');
    await page.screenshot({path:path.join(artifacts,'desktop-live-real.png'),fullPage:true});
    await page.waitForFunction(()=>document.querySelectorAll('.segment[data-id]').length>0,{},{timeout:90000});
    const sentenceCount=await page.locator('.segment[data-id]').count();
    assert.equal(await page.locator('#recording-title').textContent(),'录音中');
    // Concurrent pause/finish must share one flush, preserving the final audio.
    await page.evaluate(()=>{document.querySelector('#mic-button').click();document.querySelector('#finish-button').click();});
    await page.waitForSelector('#session-status:text-is("已完成")',{timeout:180000});
    const id=await page.evaluate(()=>localStorage.getItem('tingjian.lastMeeting'));
    const meeting=await (await fetch(`${origin}/api/meetings/${id}`)).json();
    const health=await (await fetch(`${origin}/api/health`)).json();
    assert.deepEqual(errors,[]);
    const result={device:health.device,model:health.asr_model,models_loaded:health.models_loaded,
      source:'WAV fixture looped through browser AudioContext as microphone input',
      prepare_ms:started-prepareStarted,first_subtitle_ms:firstSubtitleMs,first_text:firstText,
      sentences_before_stop:sentenceCount,segments:meeting.segments.map(s=>s.text),
      summary_count:meeting.summaries.length,errors,
      note:'Single local compatibility/latency check with an audio fixture; not a physical microphone or an ASR accuracy benchmark.'};
    fs.writeFileSync(path.join(artifacts,'live-browser-real.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
