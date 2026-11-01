// Browser audio plumbing: mic capture, WAV encoding, resampling, streamed PCM playback, level meter.

export function resample(x, from, to) {
  if (from === to) return x;
  const n = Math.round((x.length * to) / from);
  const out = new Float32Array(n);
  const ratio = from / to;
  for (let i = 0; i < n; i++) {
    const p = i * ratio, i0 = Math.floor(p), i1 = Math.min(i0 + 1, x.length - 1);
    out[i] = x[i0] + (x[i1] - x[i0]) * (p - i0);
  }
  return out;
}

export function floatToPcm16(x) {
  const out = new Int16Array(x.length);
  for (let i = 0; i < x.length; i++) {
    const s = Math.max(-1, Math.min(1, x[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function pcm16ToB64(pcm) {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

export function b64ToPcm16(b64) {
  const s = atob(b64);
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
  return new Int16Array(bytes.buffer, 0, bytes.length >> 1);
}

export function encodeWav(float32, rate) {
  const pcm = floatToPcm16(float32);
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: 'audio/wav' });
}

// Any browser-decodable audio file → mono 16 kHz WAV blob (what the model's audio encoder expects).
export async function fileToWav(file, rate = 16000) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const audio = await ctx.decodeAudioData(await file.arrayBuffer());
    const mono = new Float32Array(audio.length);
    for (let c = 0; c < audio.numberOfChannels; c++) {
      const d = audio.getChannelData(c);
      for (let i = 0; i < d.length; i++) mono[i] += d[i] / audio.numberOfChannels;
    }
    return { blob: encodeWav(resample(mono, audio.sampleRate, rate), rate), seconds: audio.duration };
  } finally {
    ctx.close();
  }
}

export function blobToB64(blob) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result.slice(r.result.indexOf(',') + 1));
    r.onerror = rej;
    r.readAsDataURL(blob);
  });
}

// Microphone capture. onChunk(Float32Array, sampleRate) fires ~every 128 samples batch; analyser feeds the visualizer.
export class Mic {
  constructor({ onChunk } = {}) {
    this.onChunk = onChunk;
    this.chunks = [];
    this.analyser = null;
    this.rate = 0;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.rate = this.ctx.sampleRate;
    await this.ctx.audioWorklet.addModule(new URL('./mic-worklet.js', import.meta.url).href); // a file of our own: the page's policy allows no blob: scripts
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.node = new AudioWorkletNode(this.ctx, 'tap');
    this.node.port.onmessage = (e) => {
      this.chunks.push(e.data);
      this.onChunk?.(e.data, this.rate);
    };
    src.connect(this.analyser);
    src.connect(this.node);
    this.start_t = performance.now();
  }

  // Stop and return everything captured as one Float32Array.
  async stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.node?.disconnect();
    await this.ctx?.close();
    const len = this.chunks.reduce((n, c) => n + c.length, 0);
    const all = new Float32Array(len);
    let o = 0;
    for (const c of this.chunks) { all.set(c, o); o += c.length; }
    this.chunks = [];
    return { samples: all, rate: this.rate, seconds: len / (this.rate || 1) };
  }
}

// Gapless playback of streamed PCM16 chunks.
export class PcmPlayer {
  constructor() { this.ctx = null; this.t = 0; this.sources = new Set(); }

  _ensure() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === 'suspended') this.ctx.resume();
  }

  push(pcm16, rate) {
    this._ensure();
    const f = new Float32Array(pcm16.length);
    for (let i = 0; i < f.length; i++) f[i] = pcm16[i] / 32768;
    const buf = this.ctx.createBuffer(1, f.length, rate);
    buf.copyToChannel(f, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    const at = Math.max(this.ctx.currentTime + 0.05, this.t);
    src.start(at);
    this.t = at + buf.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  get playing() { return this.ctx && this.t > this.ctx.currentTime; }

  stop() {
    this.sources.forEach((s) => { try { s.stop(); } catch { /* already ended */ } });
    this.sources.clear();
    this.t = 0;
  }
}

// Draw a live level meter for `analyser` into `canvas` until the returned stop() is called.
export function drawLevels(canvas, analyser) {
  const g = canvas.getContext('2d');
  const data = new Uint8Array(analyser.frequencyBinCount);
  let raf, running = true;
  const css = getComputedStyle(document.documentElement);
  const frame = () => {
    if (!running) return;
    const dpr = devicePixelRatio || 1;
    const w = (canvas.width = canvas.clientWidth * dpr), hgt = (canvas.height = canvas.clientHeight * dpr);
    analyser.getByteFrequencyData(data);
    g.clearRect(0, 0, w, hgt);
    const bars = 64, bw = w / bars;
    const grad = g.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, css.getPropertyValue('--accent').trim());
    grad.addColorStop(1, css.getPropertyValue('--accent-2').trim());
    g.fillStyle = grad;
    for (let i = 0; i < bars; i++) {
      const v = data[Math.floor((i / bars) * data.length * 0.6)] / 255;
      const bh = Math.max(3 * dpr, v * hgt * 0.95);
      g.fillRect(i * bw + bw * 0.2, (hgt - bh) / 2, bw * 0.6, bh);
    }
    raf = requestAnimationFrame(frame);
  };
  frame();
  return () => { running = false; cancelAnimationFrame(raf); g.clearRect(0, 0, canvas.width, canvas.height); };
}
