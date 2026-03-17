document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("table tbody tr").forEach((row, index) => {
    row.style.animation = `fadeIn 300ms ease ${index * 20}ms both`;
  });
});

