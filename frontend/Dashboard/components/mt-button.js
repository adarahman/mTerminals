// ═══════════════════════════════════════════════════════════════════
// mt-button.js
// First real component from DESIGN_SYSTEM.md section 20's "Original
// vision" (MTButton/MTCard/etc.) — everything unified in components.css
// so far (2026-08-05: badges, buttons, tables, modals, popovers, toasts,
// cards, form fields, carets, stat cards) is CSS-only: shared selectors
// layered onto existing hand-written HTML, not an actual JS component
// with a real API. This is the first one of those.
//
// MTButton wraps the two button primitives components.css already
// established (BUTTONS section: .mt-btn-primary, .u-toggle-btn/
// .u-toggle-group) behind one factory function, and implements
// DESIGN_SYSTEM.md section 15's required state set — default, hover,
// active, focus, disabled, loading, error — as real behavior instead of
// each call site re-deriving it by hand. It deliberately does NOT touch
// .sec-btn (nav-rail icon buttons — see DESIGN_SYSTEM.md section 20's
// Button row: checked 2026-08-05 and found to be a structurally
// distinct archetype, not a variant of this component).
//
// NOT YET WIRED into DashboardPro.html. Existing .pt-submit/
// .bt-field-run/.pt-toggle-btn buttons keep working exactly as they do
// today — swapping any of them over to MTButton is a separate,
// deliberate follow-up (new markup + a behavior check per button), not
// something this file does on its own by being loaded.
//
// Usage:
//   const btn = MTButton({
//     label: 'Place Order',
//     variant: 'primary',       // 'primary' | 'toggle'
//     onClick: () => submitOrder(),
//   });
//   document.getElementById('pt-order-panel').appendChild(btn);
//
//   btn.setLoading(true);   // shows a spinner, disables interaction
//   btn.setDisabled(true);  // disabled state, no spinner
//   btn.setError('Order rejected: margin insufficient');
//   btn.setLabel('Retry');
//
//   // Toggle-group usage — pass the full set of options, MTButton
//   // wires the .active class and click wiring for you:
//   const group = MTButtonToggleGroup({
//     options: [{ value: 'BUY', label: 'BUY' }, { value: 'SELL', label: 'SELL' }],
//     value: 'BUY',
//     onChange: (value) => setOrderSide(value),
//   });
// ═══════════════════════════════════════════════════════════════════

/**
 * Creates a single MTButton — a primary-action button using the shared
 * .mt-btn-primary base from components.css.
 *
 * @param {Object} opts
 * @param {string} opts.label - Button text.
 * @param {'primary'} [opts.variant='primary'] - Only 'primary' is a
 *   standalone single-button variant; toggle groups use
 *   MTButtonToggleGroup() below instead, since a toggle button only
 *   makes sense as part of a group.
 * @param {Function} [opts.onClick] - Click handler. Not called while
 *   disabled or loading.
 * @param {boolean} [opts.disabled=false] - Initial disabled state.
 * @param {string} [opts.type='button'] - Native button type attribute.
 * @param {string} [opts.id] - Optional element id.
 * @returns {HTMLButtonElement} A real <button>, with setLabel/
 *   setDisabled/setLoading/setError/destroy methods attached.
 */
function MTButton(opts) {
  const {
    label = '',
    variant = 'primary',
    onClick = null,
    disabled = false,
    type = 'button',
    id = null,
  } = opts || {};

  if (variant !== 'primary') {
    Logger?.error?.('mt-button', `MTButton: unknown variant "${variant}", falling back to "primary"`);
  }

  const btn = document.createElement('button');
  btn.type = type;
  btn.className = 'mt-btn-primary';
  if (id) btn.id = id;

  const labelSpan = document.createElement('span');
  labelSpan.className = 'mt-btn-label';
  labelSpan.textContent = label;
  btn.appendChild(labelSpan);

  // Spinner is built once and toggled via hidden/shown rather than
  // added/removed from the DOM on every setLoading() call, so rapid
  // loading-state flips (e.g. a fast API round-trip) don't thrash layout.
  const spinner = document.createElement('span');
  spinner.className = 'mt-btn-spinner';
  spinner.hidden = true;
  btn.appendChild(spinner);

  let isLoading = false;
  let errorTimer = null;

  btn.addEventListener('click', (e) => {
    if (btn.disabled || isLoading) return;
    if (onClick) onClick(e);
  });

  btn.setLabel = function (text) {
    labelSpan.textContent = text;
  };

  btn.setDisabled = function (value) {
    btn.disabled = !!value;
  };

  btn.setLoading = function (value) {
    isLoading = !!value;
    btn.disabled = isLoading;
    spinner.hidden = !isLoading;
    labelSpan.style.visibility = isLoading ? 'hidden' : '';
    btn.classList.toggle('mt-btn-loading', isLoading);
  };

  // Error state per DESIGN_SYSTEM.md section 15's required state set.
  // Flashes the button's border/text to --neg via a CSS class rather
  // than replacing the label, so the user still sees what they clicked;
  // pass a message to also swap the label briefly (e.g. a rejection
  // reason), or omit it to just flash the border.
  btn.setError = function (message, holdMs = 2200) {
    btn.classList.add('mt-btn-error');
    if (errorTimer) clearTimeout(errorTimer);
    const prevLabel = labelSpan.textContent;
    if (message) labelSpan.textContent = message;
    errorTimer = setTimeout(() => {
      btn.classList.remove('mt-btn-error');
      if (message) labelSpan.textContent = prevLabel;
      errorTimer = null;
    }, holdMs);
  };

  btn.destroy = function () {
    if (errorTimer) clearTimeout(errorTimer);
    btn.remove();
  };

  if (disabled) btn.setDisabled(true);

  return btn;
}

/**
 * Creates a toggle-button group (e.g. BUY/SELL) using the shared
 * .u-toggle-group/.u-toggle-btn base from components.css.
 *
 * @param {Object} opts
 * @param {{value:string, label:string}[]} opts.options
 * @param {string} opts.value - Currently-active option's value.
 * @param {Function} [opts.onChange] - Called with the new value when
 *   the user picks a different option. Not called on re-selecting the
 *   already-active option.
 * @param {string} [opts.id]
 * @returns {HTMLDivElement} The group container, with a setValue()
 *   method attached for programmatic changes (e.g. resetting the form).
 */
function MTButtonToggleGroup(opts) {
  const { options = [], value = null, onChange = null, id = null } = opts || {};

  const group = document.createElement('div');
  group.className = 'u-toggle-group';
  if (id) group.id = id;

  let currentValue = value;
  const buttons = new Map();

  options.forEach((opt) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'u-toggle-btn';
    b.textContent = opt.label;
    b.classList.toggle('active', opt.value === currentValue);
    b.addEventListener('click', () => {
      if (opt.value === currentValue) return;
      group.setValue(opt.value);
      if (onChange) onChange(opt.value);
    });
    buttons.set(opt.value, b);
    group.appendChild(b);
  });

  group.setValue = function (newValue) {
    currentValue = newValue;
    for (const [val, b] of buttons) {
      b.classList.toggle('active', val === newValue);
    }
  };

  group.getValue = function () {
    return currentValue;
  };

  return group;
}
