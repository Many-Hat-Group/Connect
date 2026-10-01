// Dates in the filter mean the admin's local days: send the timezone offset.
const tz = document.getElementById("tz");
if (tz && !tz.value) tz.value = String(new Date().getTimezoneOffset());
document.getElementById("audit-filters").addEventListener("submit", () => {
  tz.value = String(new Date().getTimezoneOffset());
});
