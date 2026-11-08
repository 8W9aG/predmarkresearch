// Crosshair and tooltip for the site's SVG line charts. Every value is also in the runs table.
document.querySelectorAll("svg[data-chart]").forEach((svg) => {
  const wrap = svg.parentElement;
  const data = JSON.parse(document.getElementById(svg.dataset.chart + "-data").textContent);
  const tip = wrap.querySelector(".tooltip");
  const cross = svg.querySelector(".crosshair");
  const hit = svg.querySelector(".hit");
  hit.setAttribute("tabindex", "0");

  function show(i) {
    const x = data.x[i];
    cross.setAttribute("x1", x);
    cross.setAttribute("x2", x);
    cross.setAttribute("visibility", "visible");
    tip.replaceChildren();
    const date = document.createElement("div");
    date.className = "tt-date";
    date.textContent = data.dates[i];
    tip.appendChild(date);
    data.series.forEach((s) => {
      const row = document.createElement("div");
      row.className = "tt-row";
      const key = document.createElement("span");
      key.className = "swatch s" + s.slot;
      const value = document.createElement("span");
      value.className = "tt-value";
      value.textContent = s.text[i];
      const label = document.createElement("span");
      label.className = "tt-label";
      label.textContent = s.label;
      row.append(key, value, label);
      tip.appendChild(row);
    });
    tip.hidden = false;
    const scale = svg.getBoundingClientRect().width / data.width;
    const left = x * scale + 8;
    const room = wrap.clientWidth - tip.offsetWidth - 8;
    tip.style.left = Math.max(8, Math.min(left, room)) + "px";
  }

  function hide() {
    cross.setAttribute("visibility", "hidden");
    tip.hidden = true;
  }

  function nearest(clientX) {
    const box = svg.getBoundingClientRect();
    const x = (clientX - box.left) * (data.width / box.width);
    let best = 0;
    data.x.forEach((px, i) => {
      if (Math.abs(px - x) < Math.abs(data.x[best] - x)) best = i;
    });
    return best;
  }

  let focused = data.x.length - 1;
  hit.addEventListener("pointermove", (ev) => show(nearest(ev.clientX)));
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => show(focused));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowLeft") focused = Math.max(0, focused - 1);
    else if (ev.key === "ArrowRight") focused = Math.min(data.x.length - 1, focused + 1);
    else return;
    ev.preventDefault();
    show(focused);
  });
});
