"use strict";

// 案内役マスへの質問。答えは、サーバーにあらかじめ書いてある文だけ。
// このファイルが動かなくても、フォームはそのまま送れて、答えの画面が開く。
function enableGuideAsk() {
  const forms = document.querySelector(".masu-guide .guide-ask-forms");
  if (!forms || !window.masuGuide) return;
  const choices = forms.querySelector(".guide-ask-choices");
  const field = forms.querySelector('input[name="question"]');
  let busy = false;

  function renderChoices(items) {
    choices.querySelectorAll("button").forEach(button => button.remove());
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "submit";
      button.name = "faq";
      button.value = item.key;
      button.className = "secondary outline";
      button.textContent = item.question;
      choices.append(button);
    }
  }

  forms.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    const body = new FormData(event.target, event.submitter);
    busy = true;
    window.masuGuide.setWaiting(true);
    try {
      const response = await fetch(event.target.action, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(String(response.status));
      const reply = await response.json();
      renderChoices(reply.next);
      field.value = "";
      window.masuGuide.say(reply.answer, reply.kind === "none" ? "worried" : null);
    } catch {
      window.masuGuide.say("いまは答えを取りに行けませんでした。少しおいて、もう一度聞いてください。", "worried");
    } finally {
      busy = false;
      window.masuGuide.setWaiting(false);
    }
  });
}

if (window.masuGuide) enableGuideAsk();
else document.addEventListener("masu:ready", enableGuideAsk, { once: true });
