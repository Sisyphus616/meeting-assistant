// 16kHz mono PCM16 frames. AudioWorklet stays off the main UI thread.
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.frame = [];
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        if (this.frame.length) this.emit();
        this.port.postMessage({flushed: true});
      }
    };
  }
  emit() {
    const frame = Int16Array.from(this.frame);
    this.port.postMessage({pcm: frame.buffer}, [frame.buffer]);
    this.frame = [];
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    const ratio = sampleRate / 16000;
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i]; this.count++; this.phase++;
      if (this.phase >= ratio) {
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.frame.push(Math.round(value * (value < 0 ? 32768 : 32767)));
        this.phase -= ratio; this.sum = 0; this.count = 0;
        if (this.frame.length === 320) this.emit();
      }
    }
    return true;
  }
}
registerProcessor('pcm-processor', PCMProcessor);
