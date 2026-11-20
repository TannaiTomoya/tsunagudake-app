"use strict";

const guide = document.querySelector(".masu-guide");
const trigger = guide.querySelector("summary");
const controls = guide.querySelector(".guide-controls");
const next = guide.querySelector(".guide-next");
const lines = [...guide.querySelectorAll(".guide-lines > p")];
const count = guide.querySelector(".guide-count");
const voiceControl = guide.querySelector(".guide-voice");
const voiceSelect = voiceControl.querySelector("select");
const synth = window.speechSynthesis;
const presets = {
  calm: { rate: 0.9, pitch: 0.9, index: 0 },
  clear: { rate: 1.1, pitch: 1.1, index: 1 },
  slow: { rate: 0.75, pitch: 1, index: 2 },
};
const preferenceKey = "tsunagudake.guide.voice";
let voices = [];
let opening = null;
let current = 0;

function refreshVoices() {
  const local = synth.getVoices().filter((voice) => voice.localService);
  const japanese = local.filter((voice) => /^ja/i.test(voice.lang));
  voices = japanese.length ? japanese : local.slice(0, 1);
  voiceControl.hidden = !voices.length;
}

function readLine() {
  if (!synth || !window.SpeechSynthesisUtterance) return;
  synth.cancel();
  const preset = presets[voiceSelect.value];
  if (!preset || !voices.length) return;
  const utterance = new SpeechSynthesisUtterance(lines[current].textContent);
  utterance.voice = voices[preset.index % voices.length];
  utterance.lang = "ja-JP";
  utterance.rate = preset.rate;
  utterance.pitch = preset.pitch;
  synth.speak(utterance);
}

function showLine(index) {
  current = index;
  lines.forEach((line, position) => { line.hidden = position !== current; });
  count.textContent = (current + 1) + " / " + lines.length;
  guide.dataset.face = current === lines.length - 1 ? "happy" : "talk";
  guide.open = true;
  next.hidden = lines.length === 1;
  next.textContent = current === lines.length - 1 ? "最初の一言" : "次の一言";
  readLine();
}

function advance() {
  if (opening !== null) return;
  if (guide.open) {
    showLine((current + 1) % lines.length);
  } else if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    showLine(0);
  } else {
    guide.dataset.face = "think";
    opening = window.setTimeout(() => {
      opening = null;
      showLine(0);
    }, 200);
  }
}

function dismiss() {
  window.clearTimeout(opening);
  opening = null;
  guide.open = false;
  delete guide.dataset.face;
  if (synth) synth.cancel();
  trigger.focus({ preventScroll: true });
}

if (synth && window.SpeechSynthesisUtterance) {
  refreshVoices();
  synth.addEventListener("voiceschanged", refreshVoices);
  try {
    const saved = localStorage.getItem(preferenceKey);
    if (Object.hasOwn(presets, saved)) voiceSelect.value = saved;
  } catch { /* 端末に保存できなくても、この画面では選択できる。 */ }
  voiceSelect.addEventListener("change", () => {
    try { localStorage.setItem(preferenceKey, voiceSelect.value); } catch {}
    readLine();
  });
  window.addEventListener("pagehide", () => synth.cancel());
}

controls.hidden = false;
trigger.addEventListener("click", (event) => {
  event.preventDefault();
  advance();
});
next.addEventListener("click", advance);
guide.querySelector(".guide-close").addEventListener("click", dismiss);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (guide.open || opening !== null)) dismiss();
});
