// Purpose: camera table script

document.addEventListener("DOMContentLoaded", () => {
  document
    .querySelectorAll('[data-bs-toggle="tooltip"]')
    .forEach((el) => new bootstrap.Tooltip(el));

  const urlInput = document.getElementById("urlInput");
  if (urlInput) {
    urlInput.placeholder = "URL";
  }
});
