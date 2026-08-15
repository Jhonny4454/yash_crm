/**
 * A rupee field: whole numbers, no spinner, no accidents.
 *
 * `<input type="number">` brought three problems with it.
 *
 * The spinner. Those up/down arrows are useless on an amount - nobody bills
 * by nudging 3050 one rupee at a time - and they eat width in an already
 * tight dialog. Worse, a `type="number"` field changes its value when the
 * mouse wheel scrolls over it while focused, so scrolling down a long payment
 * form can silently alter a figure the operator already typed.
 *
 * The decimal point. `step="1"` only makes the browser complain on submit; it
 * does not stop "3050.86" being typed, and some browsers then hand back the
 * empty string rather than the number, so the field simply goes blank. This
 * takes digits and nothing else.
 *
 * The locale. On some keyboards the decimal key produces a comma, which a
 * number input discards silently - the operator sees their keystroke vanish
 * and has no idea why.
 *
 * So: a text field constrained to digits, with a numeric keypad on mobile.
 * The value it reports is the same string of digits every other form here
 * already expects.
 *
 * This is now the ONLY numeric control in the application - every amount,
 * count, day and number field routes through it, so no screen anywhere has a
 * spinner and no amount can be entered in paise.
 *
 * `allowDecimal` is the one exception, and it exists for rates rather than
 * amounts: GST is 2.5% in places, and TaxMaster.value is Numeric(5,2), so
 * forcing whole numbers there would quietly turn 2.5 into 2 on every invoice
 * that used it. Money never sets it.
 */
export default function MoneyInput({
  value, onChange, id, className, placeholder, required, disabled,
  autoFocus, max, allowDecimal = false, ...rest
}) {
  function handle(event) {
    // Digits only. Not a regex on the whole value - stripping as we go means
    // a pasted "₹3,050.86" becomes 305086 rather than being rejected outright
    // with no explanation, and the operator can see what happened.
    const raw = String(event.target.value || "");
    let next;

    if (allowDecimal) {
      // Digits and at most one point, and never a trailing run of them.
      const cleaned = raw.replace(/[^\d.]/g, "");
      const [head, ...tail] = cleaned.split(".");
      next = tail.length ? `${head}.${tail.join("").slice(0, 2)}` : head;
      next = next.replace(/^0+(?=\d)/, "");
    } else {
      next = raw.replace(/[^\d]/g, "").replace(/^0+(?=\d)/, "");
    }

    if (max !== undefined && next !== "" && Number(next) > Number(max)) {
      next = String(max);
    }

    /* Handed back as an EVENT, not a bare string.
     *
     * Every form in this app already has a `set("field")` helper that reads
     * event.target.value. Emitting a plain string would mean rewriting every
     * one of those call sites - more places to get wrong than there are
     * inputs. Reusing the real event object keeps the name, the type and
     * anything else a handler might read. */
    event.target.value = next;
    onChange(event);
  }

  return (
    <input
      id={id}
      type="text"
      inputMode={allowDecimal ? "decimal" : "numeric"}
      autoComplete="off"
      // Tells a browser's own validation what shape this is, without the
      // spinner that comes free with type="number".
      pattern={allowDecimal ? "[0-9]*[.]?[0-9]*" : "[0-9]*"}
      className={className}
      value={value ?? ""}
      placeholder={placeholder}
      required={required}
      disabled={disabled}
      autoFocus={autoFocus}
      onChange={handle}
      {...rest}
    />
  );
}
