// AudioWorklet: microphone float samples at the device rate -> 16 kHz mono PCM16,
// posted in 100 ms chunks (1,600 samples), with an RMS level for the meter.
// AssemblyAI accepts binary frames of 50-1000 ms.
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = 16000;
    this.ratio = sampleRate / this.target; // `sampleRate` is a global in the worklet scope
    this.chunk = new Int16Array(1600);
    this.idx = 0;
    this.phase = 0;
    this.acc = 0;
    this.accN = 0;
    this.sq = 0;
    this.sqN = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const ch = input[0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = ch[i];
      this.acc += s;
      this.accN++;
      this.sq += s * s;
      this.sqN++;
      this.phase += 1;
      if (this.phase >= this.ratio) {
        this.phase -= this.ratio;
        let v = this.accN ? this.acc / this.accN : 0; // box filter = crude low-pass before decimation
        this.acc = 0;
        this.accN = 0;
        if (v > 1) v = 1; else if (v < -1) v = -1;
        this.chunk[this.idx++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.idx === this.chunk.length) {
          const level = this.sqN ? Math.sqrt(this.sq / this.sqN) : 0;
          const out = this.chunk;
          this.port.postMessage({ pcm: out.buffer, level }, [out.buffer]);
          this.chunk = new Int16Array(1600);
          this.idx = 0;
          this.sq = 0;
          this.sqN = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-worklet", PcmWorklet);
