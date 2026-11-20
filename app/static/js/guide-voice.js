"use strict";

// 案内役マスの声。言葉ではなく、文字ごとの短い音で話す。
// 音はこの端末の中で作る。外へは何も送らない。
window.masuVoice = (() => {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  // 5音の音階。どの並びでも音がにごらない。
  const scale = [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3, 2];
  const tones = {
    poko: { wave: "sine", base: 520, length: .07, gain: .9, gap: 72 },
    piko: { wave: "triangle", base: 780, length: .05, gain: .8, gap: 64 },
    koto: { wave: "square", base: 330, length: .06, gain: .32, gap: 84, lowpass: 900 },
  };
  const silent = new Set("、。，．・「」『』（）()　 \n…―—-！!？?");
  let context = null;
  let master = null;
  let lastNote = 0;

  function unlock() {
    if (!AudioContextClass) return false;
    try {
      if (!context) {
        context = new AudioContextClass();
        master = context.createGain();
        master.gain.value = .12;
        master.connect(context.destination);
      }
      if (context.state === "suspended") context.resume();
      return true;
    } catch {
      context = null;
      return false;
    }
  }

  function note(tone, ratio, { offset = 0, stretch = 1, glide = 1 } = {}) {
    if (!context || context.state !== "running") return;
    const start = context.currentTime + offset;
    const length = tone.length * stretch;
    const oscillator = context.createOscillator();
    const envelope = context.createGain();
    oscillator.type = tone.wave;
    oscillator.frequency.setValueAtTime(tone.base * ratio, start);
    if (glide !== 1) oscillator.frequency.exponentialRampToValueAtTime(tone.base * ratio * glide, start + length);
    // 立ち上がりと消え方をなめらかにして、ぷつっという雑音を出さない。
    envelope.gain.setValueAtTime(.0001, start);
    envelope.gain.exponentialRampToValueAtTime(tone.gain, start + .005);
    envelope.gain.exponentialRampToValueAtTime(.0001, start + length);
    let tail = envelope;
    if (tone.lowpass) {
      tail = context.createBiquadFilter();
      tail.type = "lowpass";
      tail.frequency.value = tone.lowpass;
      envelope.connect(tail);
    }
    oscillator.connect(envelope);
    tail.connect(master);
    oscillator.start(start);
    oscillator.stop(start + length + .02);
  }

  // 1文字ぶんの音。同じ文は、いつも同じ旋律になる。
  function tick(mode, letter, { last = false, question = false } = {}) {
    const tone = tones[mode];
    if (!tone || silent.has(letter)) return;
    const now = performance.now();
    if (!last && now - lastNote < tone.gap - 12) return;
    lastNote = now;
    const step = letter.codePointAt(0) % (scale.length - 1);
    if (last) note(tone, scale[step + 1], { stretch: 1.7, glide: question ? 1.25 : 1 });
    else note(tone, scale[step]);
  }

  // 表情に合わせた合図。喜ぶ=上がる2音、困る=下がる2音、触る=1音。
  function cue(mode, mood) {
    const tone = tones[mode];
    if (!tone) return;
    if (mood === "happy") { note(tone, scale[2]); note(tone, scale[5], { offset: .09, stretch: 1.4 }); }
    else if (mood === "worried") { note(tone, scale[2]); note(tone, scale[0] * .75, { offset: .11, stretch: 1.6 }); }
    else note(tone, scale[3], { stretch: .8 });
  }

  // 文字を出す間隔（ミリ秒）。句読点のあとは、間をあける。
  function pause(mode, letter) {
    const base = tones[mode] ? tones[mode].gap : 30;
    if (letter === "。" || letter === "？" || letter === "！") return base + 260;
    if (letter === "、") return base + 120;
    return base;
  }

  return { available: Boolean(AudioContextClass), has: mode => Object.hasOwn(tones, mode), unlock, tick, cue, pause };
})();
