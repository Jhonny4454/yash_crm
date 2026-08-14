import { useId } from "react";

/**
 * A date input that says what it means.
 *
 * The native control renders in the BROWSER's locale, so on a machine set to
 * US English it shows and accepts mm/dd/yyyy. "08/11/2026" is 11 August to
 * the operator typing it and 8 November to the field - a silent
 * off-by-three-months on an expiry date, which is the sort of mistake that
 * only surfaces when a customer is cut off early. So the date is echoed
 * underneath in a form that cannot be read two ways: "11 Aug 2026".
 *
 * There was a year dropdown here too. The native picker already covers the
 * year perfectly well, so it was one control too many - removed.
 *
 * The value in and out is always an ISO yyyy-mm-dd string, exactly as before,
 * so every form that already submits one keeps working untouched.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-08-11" -> "11 Aug 2026". Never guesses at an ambiguous string. */
export function readableDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) return "";
  const [, y, m, d] = match;
  const month = MONTHS[Number(m) - 1];
  return month ? `${d} ${month} ${y}` : "";
}

export default function DateField({
  value, onChange, id, label, required, disabled, min, max, className, hint,
}) {
  const auto = useId();
  const inputId = id || auto;
  const iso = String(value || "");

  return (
    <div className={`date-field${className ? ` ${className}` : ""}`}>
      {label && <label htmlFor={inputId}>{label}</label>}
      <div className="date-field-row">
        <input id={inputId} type="date" value={iso} required={required}
               disabled={disabled} min={min} max={max}
               onChange={(event) => onChange(event.target.value)} />
      </div>
      {iso
        ? <small className="date-field-echo">{readableDate(iso)}</small>
        : hint ? <small className="date-field-echo muted">{hint}</small> : null}
    </div>
  );
}
