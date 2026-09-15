(() => {
  "use strict";

  // Background starfield -----------------------------------------------------
  const bg = document.getElementById("starfield");
  const bctx = bg.getContext("2d");
  let stars = [];
  let last = 0;

  function resizeBg() {
    const d = Math.min(devicePixelRatio || 1, 2);
    bg.width = innerWidth * d; bg.height = innerHeight * d;
    bg.style.width = innerWidth + "px"; bg.style.height = innerHeight + "px";
    bctx.setTransform(d, 0, 0, d, 0, 0);
    const count = Math.min(360, Math.floor(innerWidth * innerHeight / 5200));
    stars = Array.from({ length: count }, (_, i) => ({
      x: Math.random() * innerWidth, y: Math.random() * innerHeight,
      r: Math.random() * 1.35 + 0.15, a: Math.random() * 0.75 + 0.15, p: i % 7 === 0,
    }));
  }
  function drawBg(t) {
    if (t - last > 32) {
      last = t;
      bctx.clearRect(0, 0, innerWidth, innerHeight);
      for (const s of stars) {
        bctx.globalAlpha = s.a * (0.7 + 0.3 * Math.sin(t * 0.0007 + s.x));
        bctx.fillStyle = s.p ? "#8adfff" : "#ffffff";
        bctx.beginPath(); bctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); bctx.fill();
      }
      bctx.globalAlpha = 1;
    }
    requestAnimationFrame(drawBg);
  }
  addEventListener("resize", resizeBg);
  resizeBg();
  requestAnimationFrame(drawBg);

  // Atom brain structure -----------------------------------------------------
  const SHELL_COLOR = {
    identity: "#67f8ff",
    cognition: "#9d7bff",
    senses: "#5fd0a0",
    hands: "#ffab4d",
    growth: "#ff7fd0",
  };

  window.DQUniverse = {
    data: null,          // legacy snapshot, still used for molecules and level
    brain: null,         // shell structure
    canvas: null,
    ctx: null,
    anim: null,
    hitboxes: [],        // [{x, y, r, node, shell}] rebuilt each frame
    hover: null,
    selected: null,
    onSelect: null,

    setData(data) { this.data = data; this.drawLoop(); },
    setBrain(brain) { this.brain = brain; this.drawLoop(); },

    init() {
      this.canvas = document.getElementById("universeCanvas");
      if (!this.canvas) return;
      this.ctx = this.canvas.getContext("2d");
      new ResizeObserver(() => this.resize()).observe(this.canvas);
      this.resize();
      this.canvas.addEventListener("mousemove", e => this.pick(e, false));
      this.canvas.addEventListener("click", e => this.pick(e, true));
      this.canvas.addEventListener("mouseleave", () => { this.hover = null; });
      this.drawLoop();
    },

    resize() {
      if (!this.canvas) return;
      const r = this.canvas.getBoundingClientRect();
      const d = Math.min(devicePixelRatio || 1, 2);
      this.canvas.width = Math.max(1, r.width * d);
      this.canvas.height = Math.max(1, r.height * d);
      this.ctx.setTransform(d, 0, 0, d, 0, 0);
    },

    pick(event, commit) {
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      let found = null;
      for (const box of this.hitboxes) {
        const dx = x - box.x, dy = y - box.y;
        if (dx * dx + dy * dy <= (box.r + 6) * (box.r + 6)) { found = box; break; }
      }
      this.hover = found;
      this.canvas.style.cursor = found ? "pointer" : "default";
      if (commit) {
        this.selected = found;
        if (this.onSelect) this.onSelect(found ? { node: found.node, shell: found.shell } : null);
      }
    },

    drawLoop() {
      if (this.anim) return;
      const tick = t => { this.draw(t); this.anim = requestAnimationFrame(tick); };
      this.anim = requestAnimationFrame(tick);
    },

    // Node colour encodes real state, not decoration: a module switched off is
    // dim, a subsystem that reports degraded capability is amber, and only a
    // genuinely working subsystem gets its shell colour.
    nodeStyle(node, shellName) {
      if (!node.enabled) return { fill: "rgba(150,160,184,.30)", stroke: "rgba(150,160,184,.45)", label: "rgba(200,210,230,.45)" };
      if (node.degraded) return { fill: "rgba(255,171,77,.75)", stroke: "rgba(255,171,77,.9)", label: "rgba(255,225,190,.92)" };
      const base = SHELL_COLOR[shellName] || "#67f8ff";
      return { fill: base, stroke: base, label: "rgba(225,240,255,.92)" };
    },

    draw(t) {
      if (!this.canvas || !this.ctx) return;
      const rect = this.canvas.getBoundingClientRect();
      const w = rect.width, h = rect.height, c = this.ctx;
      c.clearRect(0, 0, w, h);
      this.hitboxes = [];

      const atom = (this.brain && this.brain.nucleus) || (this.data && this.data.atom) || { level: 1, energy: 0.3, radius: 36 };
      const cx = w * 0.46, cy = h * 0.5;
      const core = Math.max(22, Math.min(atom.radius || 36, 64));
      const shells = (this.brain && this.brain.shells) || [];
      // Leave room for the outermost shell plus its labels.
      const span = Math.min(w, h) * 0.5 - 28;
      const step = shells.length ? Math.max(34, (span - core) / shells.length) : 46;

      // Ambient glow
      const glow = c.createRadialGradient(cx, cy, 0, cx, cy, span + core);
      glow.addColorStop(0, "rgba(95,236,255,.15)");
      glow.addColorStop(0.42, "rgba(104,70,255,.06)");
      glow.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = glow;
      c.beginPath(); c.arc(cx, cy, span + core, 0, Math.PI * 2); c.fill();

      // Shells, outermost first so inner ones draw on top
      shells.forEach((shell, shellIndex) => {
        const radius = core + step * (shellIndex + 1);
        const colour = SHELL_COLOR[shell.name] || "#67f8ff";

        c.strokeStyle = colour.replace(")", ",.16)").replace("#", "rgba(") === colour ? "rgba(120,180,255,.14)" : "rgba(255,255,255,.10)";
        c.lineWidth = 1;
        c.setLineDash([3, 7]);
        c.beginPath(); c.arc(cx, cy, radius, 0, Math.PI * 2); c.stroke();
        c.setLineDash([]);

        const nodes = shell.nodes || [];
        // Slow, per-shell rotation with alternating direction so the layers
        // stay visually distinct without becoming distracting.
        const spin = t * 0.00004 * (shellIndex % 2 ? -1 : 1) + shellIndex * 0.6;
        nodes.forEach((node, nodeIndex) => {
          const angle = spin + (nodeIndex * Math.PI * 2) / Math.max(1, nodes.length);
          const x = cx + Math.cos(angle) * radius;
          const y = cy + Math.sin(angle) * radius;
          const style = this.nodeStyle(node, shell.name);
          // Mass grows the node, but with a hard cap so one large subsystem
          // cannot swamp the picture.
          const size = 5 + Math.min(9, Math.sqrt(Math.max(0, node.mass)) * 1.1);

          c.strokeStyle = "rgba(255,255,255,.07)";
          c.beginPath(); c.moveTo(cx, cy); c.lineTo(x, y); c.stroke();

          const active = this.hover && this.hover.node === node;
          const chosen = this.selected && this.selected.node === node;
          if (active || chosen) {
            c.shadowBlur = 16; c.shadowColor = style.stroke;
          }
          c.fillStyle = style.fill;
          c.beginPath(); c.arc(x, y, size, 0, Math.PI * 2); c.fill();
          c.shadowBlur = 0;
          if (chosen) {
            c.strokeStyle = "#ffffff"; c.lineWidth = 1.5;
            c.beginPath(); c.arc(x, y, size + 4, 0, Math.PI * 2); c.stroke();
          }

          c.fillStyle = style.label;
          c.font = "10px system-ui, sans-serif";
          c.textAlign = x < cx ? "right" : "left";
          c.fillText(node.name, x + (x < cx ? -(size + 5) : size + 5), y + 3);
          c.textAlign = "start";

          this.hitboxes.push({ x, y, r: size, node, shell });
        });
      });

      // Project molecules, drifting outside the shells
      const molecules = (this.brain && this.brain.molecules) || (this.data && this.data.molecules) || [];
      molecules.slice(0, 10).forEach((m, i) => {
        const angle = t * 0.00002 * (i % 2 ? 1 : -1) + i * (Math.PI * 2 / Math.max(1, Math.min(molecules.length, 10)));
        const radius = span + core - 6;
        const x = cx + Math.cos(angle) * radius;
        const y = cy + Math.sin(angle) * radius * 0.94;
        c.fillStyle = "rgba(157,123,255,.7)";
        c.beginPath(); c.arc(x, y, 3 + Math.min(6, (m.mass || 1) * 0.28), 0, Math.PI * 2); c.fill();
        c.fillStyle = "rgba(205,195,255,.6)";
        c.font = "9px system-ui, sans-serif";
        c.fillText(String(m.name).slice(0, 18), x + 8, y + 3);
      });

      // Enrolled devices: the real DaQauntum network today
      const devices = ((this.brain && this.brain.network) || {}).devices || [];
      devices.slice(0, 6).forEach((device, i) => {
        const x = w - 34;
        const y = 42 + i * 26;
        c.fillStyle = device.active ? "rgba(124,255,190,.85)" : "rgba(150,160,184,.5)";
        c.beginPath(); c.arc(x, y, 5, 0, Math.PI * 2); c.fill();
        c.strokeStyle = "rgba(124,255,190,.18)";
        c.beginPath(); c.moveTo(cx, cy); c.lineTo(x, y); c.stroke();
        c.fillStyle = "rgba(200,230,255,.7)";
        c.font = "9px system-ui, sans-serif";
        c.textAlign = "right";
        c.fillText(String(device.name).slice(0, 16), x - 9, y + 3);
        c.textAlign = "start";
      });

      // Nucleus
      c.shadowBlur = 26; c.shadowColor = "#67f8ff";
      const g = c.createRadialGradient(cx - core * 0.25, cy - core * 0.25, 2, cx, cy, core);
      g.addColorStop(0, "#e6ffff");
      g.addColorStop(0.18, "#67f8ff");
      g.addColorStop(0.53, "#6252dc");
      g.addColorStop(1, "#140a35");
      c.fillStyle = g;
      c.beginPath(); c.arc(cx, cy, core, 0, Math.PI * 2); c.fill();
      c.shadowBlur = 0;
      c.fillStyle = "#fff";
      c.textAlign = "center";
      c.font = `700 ${Math.max(12, core * 0.36)}px system-ui, sans-serif`;
      c.fillText("DQ", cx, cy + 4);
      c.font = "9px system-ui, sans-serif";
      c.fillStyle = "rgba(230,255,255,.75)";
      c.fillText(`L${atom.level || 1}`, cx, cy + core * 0.36 + 14);
      c.textAlign = "start";

      // Hover tooltip
      if (this.hover) {
        const { node, shell, x, y } = this.hover;
        const lines = [node.name, node.detail || "", shell.title];
        c.font = "11px system-ui, sans-serif";
        const width = Math.max(...lines.map(l => c.measureText(l).width)) + 18;
        const height = 18 * lines.length + 10;
        const bx = Math.min(Math.max(8, x + 14), w - width - 8);
        const by = Math.min(Math.max(8, y - height - 10), h - height - 8);
        c.fillStyle = "rgba(12,16,30,.94)";
        c.strokeStyle = "rgba(255,255,255,.14)";
        c.beginPath();
        if (c.roundRect) c.roundRect(bx, by, width, height, 8); else c.rect(bx, by, width, height);
        c.fill(); c.stroke();
        lines.forEach((line, i) => {
          c.fillStyle = i === 0 ? "#eaffff" : "rgba(180,196,220,.9)";
          c.font = i === 0 ? "600 11px system-ui, sans-serif" : "11px system-ui, sans-serif";
          c.fillText(line, bx + 9, by + 18 + i * 17);
        });
      }
    },
  };

  addEventListener("DOMContentLoaded", () => window.DQUniverse.init());
})();
