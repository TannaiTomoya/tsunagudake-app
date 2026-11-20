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
