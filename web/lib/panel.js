// Shared panel hardware: knobs, rockers, chip groups, keybed.

export function makeKnob(el, initial, onChange) {
  let value = initial;

  const render = () => {
    el.style.setProperty("--angle", `${-135 + value * 270}deg`);
    el.setAttribute("aria-valuenow", value.toFixed(3));
  };

  const set = (v) => {
    value = Math.min(1, Math.max(0, v));
    render();
    onChange(value);
  };

  let dragging = false, lastY = 0;
  el.addEventListener("pointerdown", (e) => {
    dragging = true; lastY = e.clientY;
    el.setPointerCapture(e.pointerId);
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    set(value + (lastY - e.clientY) / 160);
    lastY = e.clientY;
  });
  el.addEventListener("pointerup", () => { dragging = false; });
  el.addEventListener("wheel", (e) => {
    e.preventDefault();
    set(value - Math.sign(e.deltaY) * 0.03);
  }, { passive: false });
  el.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 0.01 : 0.05;
    if (e.key === "ArrowUp" || e.key === "ArrowRight") { e.preventDefault(); set(value + step); }
    if (e.key === "ArrowDown" || e.key === "ArrowLeft") { e.preventDefault(); set(value - step); }
  });

  render();
  return { set };
}

export function makeRocker(el, initial, onChange) {
  let on = initial;
  const render = () => el.setAttribute("aria-pressed", String(on));
  el.addEventListener("click", () => { on = !on; render(); onChange(on); });
  render();
  return { get: () => on, set: (v) => { on = v; render(); } };
}

// radio-style chip group; chips are buttons with data-value
export function makeChipGroup(container, onChange) {
  const chips = [...container.querySelectorAll(".chip")];
  chips.forEach((chip) => chip.addEventListener("click", () => {
    chips.forEach((c) => {
      c.classList.toggle("selected", c === chip);
      c.setAttribute("aria-checked", String(c === chip));
    });
    onChange(chip.dataset.value ?? chip.dataset.color);
  }));
}

const BLACK_OF = { 1: true, 3: true, 6: true, 8: true, 10: true };

// mouse keybed with glissando + computer-keyboard mapping
export function buildKeybed(bed, { lo, hi, keymap = {}, onNoteOn, onNoteOff }) {
  const whites = [];
  for (let m = lo; m <= hi; m++) if (!BLACK_OF[m % 12]) whites.push(m);

  for (const m of whites) {
    const el = document.createElement("div");
    el.className = "key";
    el.dataset.midi = m;
    bed.appendChild(el);
  }
  for (let m = lo; m <= hi; m++) {
    if (!BLACK_OF[m % 12]) continue;
    const el = document.createElement("div");
    el.className = "key black";
    el.dataset.midi = m;
    const before = whites.filter((w) => w < m).length;
    el.style.left = `calc(${(before / whites.length) * 100}% - 8px)`;
    bed.appendChild(el);
  }

  const keyEl = (m) => bed.querySelector(`[data-midi="${m}"]`);
  const down = (m) => { keyEl(m)?.classList.add("down"); onNoteOn(m); };
  const up = (m) => { keyEl(m)?.classList.remove("down"); onNoteOff(m); };

  let pointerNote = null;
  const press = (el) => {
    const m = Number(el.dataset.midi);
    if (pointerNote === m) return;
    if (pointerNote !== null) up(pointerNote);
    pointerNote = m;
    down(m);
  };
  const release = () => {
    if (pointerNote === null) return;
    up(pointerNote);
    pointerNote = null;
  };

  bed.addEventListener("pointerdown", (e) => {
    const key = e.target.closest(".key");
    if (key) { e.preventDefault(); press(key); }
  });
  bed.addEventListener("pointerover", (e) => {
    const key = e.target.closest(".key");
    if (key && e.buttons) press(key);
  });
  window.addEventListener("pointerup", release);
  bed.addEventListener("pointerleave", release);

  window.addEventListener("keydown", (e) => {
    if (e.repeat || e.metaKey || e.ctrlKey) return;
    const m = keymap[e.key];
    if (m !== undefined) down(m);
  });
  window.addEventListener("keyup", (e) => {
    const m = keymap[e.key];
    if (m !== undefined) up(m);
  });
}
