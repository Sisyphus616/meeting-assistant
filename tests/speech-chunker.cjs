const assert = require('node:assert/strict');
const {SpeechChunker} = require('../web/speech-chunker.js');
function fixture() {
  const previews = [], finals = [];
  let serial = 0;
  const chunker = new SpeechChunker({makeId:()=>String(++serial),
    onPreview:(pcm,start,id)=>previews.push({pcm,start,id}),
    onFinal:(pcm,start,id)=>finals.push({pcm,start,id})});
  const feed = (ms, amplitude=2000) => {
    for (let t=0;t<ms;t+=20) chunker.push(new Int16Array(320).fill(amplitude));
  };
  return {chunker,previews,finals,feed};
}
{
  const {previews,finals,feed,chunker} = fixture();
  feed(3400);
  assert.ok(previews.length>=3,'subtitles must update before any pause/stop');
  assert.equal(finals.length,0);
  feed(200,0);feed(1000);
  assert.equal(finals.length,0,'a short hesitation must not cut off the utterance');
  feed(800,0);
  assert.equal(finals.length,1);
  for (const preview of previews) {
    assert.equal(preview.id,finals[0].id);
    assert.deepEqual(finals[0].pcm.subarray(0,preview.pcm.length),preview.pcm);
  }
  assert.ok(finals[0].pcm.length<chunker.cursor,'long trailing silence is trimmed');
}
{
  const {previews,finals,feed,chunker} = fixture();
  feed(100,2000);feed(1200,0);chunker.flush();
  assert.equal(previews.length,0,'clicks and short noise must not create orphan drafts');
  assert.equal(finals.length,0);
  feed(1000,300);chunker.flush();
  assert.equal(finals.length,1,'quiet speech should pass a lower threshold');
}
{
  const {finals,feed,chunker} = fixture();
  feed(25000);chunker.flush();
  assert.equal(finals.length,3,'continuous speech is bounded without requiring stop');
  assert.ok(finals.every(f=>f.pcm.length<=192000));
  assert.equal(finals.reduce((n,f)=>n+f.pcm.length,0),400000,'hard boundaries must retain every sample');
}
console.log('SpeechChunker: rolling previews, pause handling, quiet speech, noise rejection and continuous capture passed');
