"use strict";
/* Фоновые частицы: лёгкий canvas без зависимостей.
   Дизайн-ограничения: не мешать чтению (низкая непрозрачность, за контентом),
   не жечь CPU (пауза в скрытой вкладке, ~55 частиц), уважать reduced-motion. */
(function () {
  const canvas = document.getElementById("fx");
  if (!canvas) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    canvas.remove();
    return;
  }
  // Позиционируем программно — слой не должен попасть в поток страницы,
  // даже если styles.css не загрузился или закэширован старым
  Object.assign(canvas.style, {
    position: "fixed", top: "0", left: "0", right: "0", bottom: "0",
    zIndex: "-1", pointerEvents: "none",
  });
  const ctx = canvas.getContext("2d");
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  let W = 0, H = 0, parts = [], raf = null;

  // плотность частиц привязана к площади окна (на FullHD ≈ 140)
  const count = () => Math.max(90, Math.min(200, Math.round((W * H) / 14000)));

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * DPR; canvas.height = H * DPR;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function spawn() {
    parts = Array.from({ length: count() }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      r: 0.6 + Math.random() * 1.6,
      vx: (Math.random() - 0.5) * 0.12,
      vy: -0.05 - Math.random() * 0.18,          // медленный дрейф вверх
      tw: Math.random() * Math.PI * 2,           // фаза мерцания
      ts: 0.004 + Math.random() * 0.01,
      blue: Math.random() < 0.7,
    }));
  }

  function tick() {
    ctx.clearRect(0, 0, W, H);
    for (const p of parts) {
      p.x += p.vx; p.y += p.vy; p.tw += p.ts;
      if (p.y < -4) { p.y = H + 4; p.x = Math.random() * W; }
      if (p.x < -4) p.x = W + 4;
      if (p.x > W + 4) p.x = -4;
      const a = 0.12 + 0.18 * (0.5 + 0.5 * Math.sin(p.tw));
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.blue ? `rgba(96,165,250,${a})` : `rgba(103,232,249,${a})`;
      ctx.fill();
    }
    raf = requestAnimationFrame(tick);
  }

  function start() { if (raf == null) raf = requestAnimationFrame(tick); }
  function stop() { if (raf != null) { cancelAnimationFrame(raf); raf = null; } }

  window.addEventListener("resize", () => { resize(); spawn(); });
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));

  resize();
  spawn();
  start();
})();
