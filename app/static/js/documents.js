const removePrintCopy = () => document.querySelector(".print-copy")?.remove();
window.addEventListener("afterprint", removePrintCopy);
document.querySelectorAll(".print-button").forEach(button => {
  button.addEventListener("click", () => {
    removePrintCopy();
    const sheet = button.closest(".print-document").cloneNode(true);
    sheet.querySelector(".print-button")?.remove();
    sheet.classList.add("print-copy");
    document.body.append(sheet);
    window.print();
  });
});

for (const form of document.querySelectorAll("[data-estimate-form]")) {
  const preview = form.querySelector(".estimate-preview");
  const {estimate_hours: hours, share_percent: share, cap_yen: cap} = form.elements;
  const yen = new Intl.NumberFormat("ja-JP");
  function updateEstimate() {
    const count = hours.value === "" ? Number(form.dataset.defaultHours) : hours.valueAsNumber;
    let text = "時間・割合・上限額を確認してください";
    if (hours.validity.valid && share.validity.valid && cap.validity.valid) {
      const fee = Math.floor(count * Number(form.dataset.rate) * share.valueAsNumber / 100);
      text = yen.format(cap.value === "" ? fee : Math.min(fee, cap.valueAsNumber)) + "円";
    }
    preview.querySelector("output").value = text;
  }
  updateEstimate();
  preview.hidden = false;
  form.addEventListener("input", updateEstimate);
}
