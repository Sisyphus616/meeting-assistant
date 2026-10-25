'use strict';
// Audio packets and sentence boundaries are separate: previews may be revised;
// only a pause / length bound / explicit stop confirms the audio for storage.
class SpeechChunker {
  constructor({onPreview, onFinal, cursor = 0, makeId} = {}) {
    this.onPreview = onPreview;
    this.onFinal = onFinal;
    this.makeId = makeId || (() => crypto.randomUUID().replaceAll('-', ''));
    this.cursor = cursor * 16;
    this.pre = []; this.preSamples = 0; this.voiceRun = 0;
    this.noise = .0015; this.rms = 0;
    this.reset();
  }
  reset() {
    this.frames = []; this.samples = 0; this.voiced = 0;
    this.silence = 0; this.lastVoice = 0; this.lastPreview = 0; this.id = null;
  }
  push(pcm) {
    let sum = 0;
    for (const sample of pcm) sum += (sample / 32768) ** 2;
    this.rms = Math.sqrt(sum / pcm.length);
    this.cursor += pcm.length;
    const threshold = Math.max(this.id ? .004 : .006, this.noise * (this.id ? 1.8 : 3));
    const voice = this.rms >= threshold;
    if (!this.id) {
      if (this.rms < .006) this.noise = this.noise * .98 + this.rms * .02;
      this.pre.push(pcm); this.preSamples += pcm.length;
      while (this.preSamples > 3840 && this.pre.length > 1) this.preSamples -= this.pre.shift().length;
      this.voiceRun = voice ? this.voiceRun + pcm.length : 0;
      if (this.voiceRun < 640) return;
      this.frames = this.pre; this.samples = this.preSamples;
      this.pre = []; this.preSamples = 0;
      this.id = this.makeId(); this.start = (this.cursor - this.samples) / 16;
      this.voiced = this.voiceRun; this.voiceRun = 0;
      this.lastVoice = this.samples;
    } else {
      this.frames.push(pcm); this.samples += pcm.length;
      if (voice) { this.voiced += pcm.length; this.silence = 0; this.lastVoice = this.samples; }
      else this.silence += pcm.length;
    }
    if (this.silence >= 12800 || this.samples >= 192000) {
      this.flush();
    } else if (this.voiced >= 2560 && this.samples >= 12800 && (!this.lastPreview || this.samples - this.lastPreview >= 19200)) {
      this.lastPreview = this.samples;
      this.onPreview?.(this.snapshot(), this.start, this.id);
    }
  }
  snapshot() {
    // Keep 160ms after the last voiced frame. The same bytes can be reused by
    // the server when its last preview already covers the complete final clip.
    const count = Math.min(this.samples, this.lastVoice + 2560);
    const pcm = new Int16Array(count);
    let offset = 0;
    for (const part of this.frames) {
      const take = Math.min(part.length, count - offset);
      if (take <= 0) break;
      pcm.set(part.subarray(0, take), offset); offset += take;
    }
    return pcm;
  }
  flush() {
    const valid = this.id && this.voiced >= 2560;
    const pcm = valid ? this.snapshot() : null;
    const start = this.start, id = this.id;
    this.reset();
    if (pcm) this.onFinal?.(pcm, start, id);
  }
}
if (typeof module !== 'undefined') module.exports = {SpeechChunker};
else globalThis.SpeechChunker = SpeechChunker;
