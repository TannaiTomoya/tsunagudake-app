const tokenField = document.querySelector('input[name="token"]');
const fragment = window.location.hash.slice(1);
if (tokenField && /^[A-Za-z0-9_-]{43}$/.test(fragment)) {
  tokenField.value = fragment;
  window.history.replaceState(null, "", window.location.pathname);
}
