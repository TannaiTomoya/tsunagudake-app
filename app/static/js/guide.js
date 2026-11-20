"use strict";

function enableGuide() {
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
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const dock = guide.closest(".guide-dock");
  const portraits = [...document.querySelectorAll("svg.masu")];
  const hero = document.querySelector(".hero-masu");
  const visualLine = document.createElement("p");
  visualLine.className = "guide-typing";
  visualLine.setAttribute("aria-hidden", "true");
  guide.querySelector(".guide-lines").after(visualLine);
  guide.querySelector(".guide-lines").classList.add("guide-sr");
  let revealTimer = null;
  let speaking = null;
  let guideOpener = trigger;
  let sleepy = false;
  let looking = false;
  let stroking = null;
  let current = 0;

  function refreshVoices() {
    const local = synth.getVoices().filter((voice) => voice.localService);
    const japanese = local.filter((voice) => /^ja/i.test(voice.lang));
    voices = japanese.length ? japanese : local.slice(0, 1);
    voiceControl.hidden = !voices.length;
  }

  function readLine() {
    if (!synth || !window.SpeechSynthesisUtterance) return;
    speaking = null;
    synth.cancel();
    const preset = presets[voiceSelect.value];
    if (!preset || !voices.length) return;
    const utterance = new SpeechSynthesisUtterance(lines[current].textContent);
    utterance.voice = voices[preset.index % voices.length];
    utterance.lang = "ja-JP";
    utterance.rate = preset.rate;
    utterance.pitch = preset.pitch;
    speaking = utterance;
    utterance.onend = utterance.onerror = () => {
      if (speaking === utterance) {
        speaking = null;
        updateFaces();
      }
    };
    synth.speak(utterance);
  }

  function updateFaces() {
    const thinking = document.activeElement?.matches(".workspace input:not([type=hidden]), .workspace textarea, .workspace select");
    for (const svg of portraits) {
      let face = dock.dataset.mood;
      if (sleepy) face = "sleepy";
      else if (thinking) face = "think";
      else if (looking && face === "idle") face = "gaze";
      if (guide.open && (revealTimer !== null || speaking || reduced.matches)) face = "talk";
      if (svg === stroking) face = "happy";
      svg.dataset.face = face;
    }
  }

  function finishLine() {
    clearInterval(revealTimer);
    revealTimer = null;
    visualLine.textContent = lines[current].textContent;
    next.hidden = lines.length === 1;
    next.textContent = current === lines.length - 1 ? "最初の一言" : "次の一言";
    updateFaces();
  }

  function showLine(index) {
    current = index;
    lines.forEach((line, position) => { line.hidden = position !== current; });
    count.textContent = (current + 1) + " / " + lines.length;
    guide.open = true;
    clearInterval(revealTimer);
    const letters = Array.from(lines[index].textContent);
    visualLine.textContent = "";
    next.hidden = false;
    next.textContent = "全文を表示";
    if (reduced.matches) {
      finishLine();
    } else {
      let length = 0;
      revealTimer = setInterval(() => {
        visualLine.textContent += letters[length++];
        if (length === letters.length) finishLine();
      }, 30);
    }
    readLine();
    updateFaces();
  }

  function advance() {
    if (revealTimer !== null) finishLine();
    else showLine(guide.open ? (current + 1) % lines.length : 0);
  }

  function dismiss() {
    guide.open = false;
    finishLine();
    speaking = null;
    if (synth) synth.cancel();
    updateFaces();
    guideOpener.focus({ preventScroll: true });
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
      updateFaces();
    });
    window.addEventListener("pagehide", () => synth.cancel());
  }

  // Animate numeric values and write only SVG transform attributes. Layout stays in CSS.
  const springs = new WeakMap();
  function spring(svg, phase) {
    if (reduced.matches) return;
    let pose = springs.get(svg);
    if (!pose) {
      pose = { width: 1, height: 1, lift: 0 };
      springs.set(svg, pose);
    }
    pose.animation?.cancel();
    const frames = phase === "press"
      ? [{ width: 1.12, height: .88, lift: 0, duration: 75 }]
      : phase === "celebrate"
        ? [{ width: .96, height: 1.05, lift: -1200 / svg.getBoundingClientRect().width, duration: 160 }, { width: 1.09, height: .92, lift: 0, duration: 140 }]
        : [{ width: .94, height: 1.08, lift: -2, duration: 100 }, { width: 1.04, height: .97, lift: 0, duration: 95 }];
    if (phase !== "press") frames.push({ width: .985, height: 1.015, lift: 0, duration: 85 }, { width: 1, height: 1, lift: 0, duration: 120 });
    const body = svg.querySelector(".masu-spring");
    const shadow = svg.querySelector(".masu-shadow");
    pose.animation = window.anime.animate(pose, {
      keyframes: frames,
      ease: "outQuad",
      onUpdate: () => {
        body.setAttribute("transform", `translate(50 ${84 + pose.lift}) scale(${pose.width} ${pose.height}) translate(-50 -84)`);
        shadow.setAttribute("rx", String(26 * pose.width + pose.lift * .5));
      },
    });
  }

  let blinkTimer;
  function blink(double = false) {
    if (reduced.matches || document.hidden) return;
    const blinking = portraits.filter(svg => !["happy", "sleepy"].includes(svg.dataset.face));
    blinking.forEach(svg => svg.toggleAttribute("data-blink", true));
    setTimeout(() => {
      blinking.forEach(svg => svg.removeAttribute("data-blink"));
      if (double) setTimeout(blink, 115);
    }, 120);
  }
  function scheduleBlink() {
    clearTimeout(blinkTimer);
    if (reduced.matches || document.hidden) return;
    blinkTimer = setTimeout(() => {
      blink(Math.random() < .15);
      scheduleBlink();
    }, 4000 + Math.random() * 3000);
  }

  let lastActivity = Date.now();
  function checkIdle() {
    const remaining = 60000 - (Date.now() - lastActivity);
    if (remaining <= 0) {
      sleepy = true;
      updateFaces();
    } else {
      setTimeout(checkIdle, remaining);
    }
  }
  function wake() {
    lastActivity = Date.now();
    if (sleepy) {
      sleepy = false;
      setTimeout(checkIdle, 60000);
      updateFaces();
    }
  }
  setTimeout(checkIdle, 60000);

  let contactTimer;
  let contact = false;
  function setGaze(svg, x, y) {
    svg.querySelectorAll(".eye-pupil").forEach(pupil => {
      pupil.setAttribute("transform", `translate(${x} ${y})`);
    });
  }
  function makeContact() {
    clearTimeout(contactTimer);
    looking = true;
    contact = true;
    portraits.forEach(svg => setGaze(svg, 0, -1));
    updateFaces();
    contactTimer = setTimeout(() => {
      contact = false;
      looking = false;
      blink();
      portraits.forEach(svg => setGaze(svg, 0, 0));
      updateFaces();
    }, 3000);
  }

  let hoverFrame = 0;
  document.addEventListener("pointermove", event => {
    wake();
    if (event.pointerType === "touch" || contact || hoverFrame) return;
    hoverFrame = requestAnimationFrame(() => {
      hoverFrame = 0;
      let near = false;
      for (const svg of portraits) {
        const rect = svg.getBoundingClientRect();
        const dx = event.clientX - rect.left - rect.width / 2;
        const dy = event.clientY - rect.top - rect.height / 2;
        const distance = Math.hypot(dx, dy);
        const nearby = distance < rect.width + 90;
        near ||= nearby;
        const scale = nearby ? Math.min(3 * 100 / rect.width, 2.3) / Math.max(distance, 18) : 0;
        setGaze(svg, dx * scale, dy * scale);
      }
      if (looking !== near) { looking = near; updateFaces(); }
    });
  }, { passive: true });

  function mascotControl(button) {
    const svg = button.querySelector(".masu");
    let lastX = 0;
    let direction = 0;
    let stroked = false;
    let petTimer;
    function stopPress() {
      if (!svg.hasAttribute("data-pressed")) return;
      svg.removeAttribute("data-pressed");
      spring(svg, "release");
    }
    button.addEventListener("pointerenter", event => { lastX = event.clientX; direction = 0; });
    button.addEventListener("pointerdown", event => {
      wake();
      lastX = event.clientX;
      direction = 0;
      stroked = false;
      svg.toggleAttribute("data-pressed", true);
      spring(svg, "press");
    });
    button.addEventListener("pointermove", event => {
      const delta = event.clientX - lastX;
      if (Math.abs(delta) < 6) return;
      if (direction && Math.sign(delta) !== direction) {
        stroked = true;
        stroking = svg;
        clearTimeout(petTimer);
        petTimer = setTimeout(() => { if (stroking === svg) stroking = null; updateFaces(); }, 650);
        updateFaces();
      }
      lastX = event.clientX;
      direction = Math.sign(delta);
    });
    for (const name of ["pointerup", "pointercancel", "pointerleave"]) button.addEventListener(name, stopPress);
    button.addEventListener("click", event => {
      event.preventDefault();
      if (stroked && event.detail) { stroked = false; return; }
      if (!guide.open) guideOpener = button;
      wake();
      makeContact();
      if (!event.detail) spring(svg, "release");
      advance();
      if (button === hero) trigger.focus({ preventScroll: true });
    });
  }
  controls.hidden = false;
  mascotControl(trigger);
  if (hero) {
    mascotControl(hero);
    hero.addEventListener("keydown", event => {
      if (event.key === " ") { event.preventDefault(); hero.click(); }
    });
  }
  next.addEventListener("click", advance);
  guide.querySelector(".guide-close").addEventListener("click", dismiss);
  document.addEventListener("keydown", event => {
    wake();
    if (event.key === "Escape" && guide.open) dismiss();
  });
  document.addEventListener("pointerdown", wake, { passive: true });
  document.addEventListener("input", wake);
  document.addEventListener("focusin", () => { wake(); updateFaces(); });
  document.addEventListener("focusout", () => queueMicrotask(updateFaces));
  document.addEventListener("visibilitychange", scheduleBlink);
  reduced.addEventListener("change", () => {
    finishLine();
    scheduleBlink();
    if (reduced.matches) {
      for (const svg of portraits) {
        springs.get(svg)?.animation?.cancel();
        springs.delete(svg);
        svg.querySelector(".masu-spring").removeAttribute("transform");
        svg.querySelector(".masu-shadow").setAttribute("rx", "26");
      }
    }
  });
  updateFaces();
  scheduleBlink();

  // Consume a short-lived cue after the form submission that actually led to a success screen.
  const successKey = "tsunagudake.guide.celebration";
  document.addEventListener("submit", event => {
    try {
      if (event.submitter?.hasAttribute("data-masu-success")) sessionStorage.setItem(successKey, String(Date.now()));
      else sessionStorage.removeItem(successKey);
    } catch { /* Storage is optional. */ }
  });
  try {
    const submitted = Number(sessionStorage.getItem(successKey));
    sessionStorage.removeItem(successKey);
    if (Date.now() - submitted < 30000 && dock.dataset.mood === "happy") spring(trigger.querySelector(".masu"), "celebrate");
  } catch { /* The static expression also conveys success. */ }
}

if (window.anime?.animate) enableGuide();
