(() => {
  const root = document.documentElement;
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  const param = new URLSearchParams(window.location.search).get("scoutTheme");
  const validTheme = theme => theme === "light" || theme === "dark";
  let inheritedTheme;
  const applyTheme = () => {
    root.dataset.theme = inheritedTheme || (validTheme(param) ? param : preference.matches ? "dark" : "light");
  };
  const sendTheme = target => target.postMessage({type: "session-theme", theme: root.dataset.theme}, "*");
  const children = () => [...document.querySelectorAll("iframe")].map(frame => frame.contentWindow);
  new MutationObserver(() => children().forEach(sendTheme)).observe(root, {
    attributes: true, attributeFilter: ["data-theme"]
  });
  window.addEventListener("message", event => {
    if (window.parent !== window && event.source === window.parent &&
        event.data?.type === "session-theme" && validTheme(event.data.theme)) {
      inheritedTheme = event.data.theme;
      applyTheme();
    } else if (event.data?.type === "session-theme-request" && children().includes(event.source)) {
      sendTheme(event.source);
    }
  });
  preference.addEventListener("change", applyTheme);
  applyTheme();
  if (window.parent !== window) window.parent.postMessage({type: "session-theme-request"}, "*");
})();
