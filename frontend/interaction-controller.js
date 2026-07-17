// ============================================================
// interaction-controller.js
// User interaction controller for price chart
// Handles zoom, pan, crosshair, and UI event handlers
// ============================================================

class InteractionController {
  constructor(renderer, onZoomChange, onRenderRequest) {
    this.renderer = renderer;
    this.onZoomChange = onZoomChange;
    this.onRenderRequest = onRenderRequest;
    this._drag = null;
    this._hover = null;
    this._hoverRaf = null;
    this._panWired = false;
    this._zoomWired = false;
  }

  attachHandlers(canvas, windowButtons, settingsToggle, settingsRow) {
    if (!canvas) return;
    
    this._attachZoomHandlers(canvas);
    this._attachWindowButtonHandlers(windowButtons);
    this._attachSettingsToggle(settingsToggle, settingsRow);
    this._attachPanHandlers();
  }

  _attachZoomHandlers(canvas) {
    if (canvas._pcZoomWired) return;
    canvas._pcZoomWired = true;
    canvas.style.cursor = 'grab';

    canvas.onwheel = (e) => {
      const rc = this.renderer.getLastRenderCtx();
      if (!rc) return;
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const vAtCursor =(rc.vWindowStart + ((mouseX - rc.PAD.l) / rc.PW) * rc.vSpan);
      const tAtCursor = rc.tmap.toReal(vAtCursor);
      const curStart = this.onZoomChange().zoomStart != null ? this.onZoomChange().zoomStart : rc.windowStart;
      const curEnd = this.onZoomChange().zoomEnd != null ? this.onZoomChange().zoomEnd : rc.windowStart + rc.span;
      const zoomFactor = e.deltaY < 0 ? 0.85 : (1 / 0.85);
      const dataSpan = Math.max(rc.minSpan, rc.dataMaxT - rc.dataMinT);
      let newSpan = Math.max(rc.minSpan, Math.min((curEnd - curStart) * zoomFactor, dataSpan));
      const ratio = (curEnd > curStart) ? (tAtCursor - curStart) / (curEnd - curStart) : 0.5;
      let newStart = tAtCursor - ratio * newSpan;
      let newEnd = newStart + newSpan;
      if (newStart < rc.dataMinT) { newStart = rc.dataMinT; newEnd = newStart + newSpan; }
      if (newEnd > rc.dataMaxT) { newEnd = rc.dataMaxT; newStart = newEnd - newSpan; }
      this.onZoomChange({ zoomStart: newStart, zoomEnd: newEnd });
      this.onRenderRequest();
    };

    canvas.onmousedown = (e) => {
      const rc = this.renderer.getLastRenderCtx();
      if (!rc) return;
      const zoomState = this.onZoomChange();
      this._drag = {
        startX: e.clientX,
        winStart: zoomState.zoomStart != null ? zoomState.zoomStart : rc.windowStart,
        winEnd: zoomState.zoomEnd != null ? zoomState.zoomEnd : rc.windowStart + rc.span,
      };
      canvas.style.cursor = 'grabbing';
    };

    canvas.ondblclick = () => {
      this.onZoomChange({ zoomStart: null, zoomEnd: null });
      this.onRenderRequest();
    };

    // Crosshair hover
    canvas.onmousemove = (e) => {
      const rect = canvas.getBoundingClientRect();
      this._hover = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      if (this._hoverRaf) return;
      this._hoverRaf = requestAnimationFrame(() => { 
        this._hoverRaf = null; 
        this.onRenderRequest(); 
      });
    };

    canvas.onmouseleave = () => {
      this._hover = null;
      if (this._hoverRaf) return;
      this._hoverRaf = requestAnimationFrame(() => { 
        this._hoverRaf = null; 
        this.onRenderRequest(); 
      });
    };
  }

  _attachPanHandlers() {
    if (this._panWired) return;
    this._panWired = true;

    window.addEventListener('mousemove', (e) => {
      const drag = this._drag;
      if (!drag) return;
      const rc = this.renderer.getLastRenderCtx();
      if (!rc) return;
      const dxPx = e.clientX - drag.startX;
      const spanMs = drag.winEnd - drag.winStart;
      const dtMs = -(dxPx / rc.PW) * spanMs;
      let newStart = drag.winStart + dtMs;
      let newEnd = drag.winEnd + dtMs;
      if (newStart < rc.dataMinT) { newStart = rc.dataMinT; newEnd = newStart + spanMs; }
      if (newEnd > rc.dataMaxT) { newEnd = rc.dataMaxT; newStart = newEnd - spanMs; }
      this.onZoomChange({ zoomStart: newStart, zoomEnd: newEnd });
      this.onRenderRequest();
    });

    window.addEventListener('mouseup', () => {
      if (this._drag) {
        this._drag = null;
        const liveCanvas = document.getElementById('price-chart-canvas');
        if (liveCanvas) liveCanvas.style.cursor = 'grab';
      }
    });
  }

  _attachWindowButtonHandlers(windowButtons) {
    if (!windowButtons) return;
    windowButtons.forEach(b => {
      b.onclick = () => {
        const winBar = document.getElementById('pc-win-bar');
        if (winBar) {
          winBar.querySelectorAll('.pc-win-btn').forEach(x => x.classList.remove('pc-active'));
        }
        b.classList.add('pc-active');
        const w = PRICE_CHART_WINDOWS.find(x => x.key === b.dataset.win);
        if (!w) return;
        if (w.ms === Infinity) {
          this.onZoomChange({ zoomStart: null, zoomEnd: null });
        } else {
          const now = Date.now();
          this.onZoomChange({ zoomStart: now - w.ms, zoomEnd: now });
        }
        this.onRenderRequest();
      };
    });
  }

  _attachSettingsToggle(settingsToggle, settingsRow) {
    if (!settingsToggle || !settingsRow) return;
    settingsToggle.onclick = () => {
      const open = settingsRow.style.display !== 'none';
      settingsRow.style.display = open ? 'none' : 'flex';
      settingsToggle.classList.toggle('pc-active', !open);
    };
  }

  getHoverState() {
    return this._hover;
  }

  reset() {
    this._drag = null;
    this._hover = null;
    if (this._hoverRaf) {
      cancelAnimationFrame(this._hoverRaf);
      this._hoverRaf = null;
    }
  }
}
