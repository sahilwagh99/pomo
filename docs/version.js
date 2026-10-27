// Show the latest release's version (offline or rate-limited: the generic text stays).
(async () => {
  try {
    const res = await fetch("https://api.github.com/repos/sahilwagh99/pomo/releases/latest");
    if (!res.ok) return;
    const version = ((await res.json()).tag_name || "").replace(/^v/, "");
    if (!version) return;
    document.querySelectorAll(".version").forEach((el, i) => {
      el.textContent = i === 0 ? `Version ${version}` : `Pomo ${version} for Windows`;
    });
  } catch (e) { /* keep the generic text */ }
})();
