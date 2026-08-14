import { useEffect, useState } from "react";
import { get } from "../../api/client";
import { inr } from "../ui";
import MoneyInput from "../MoneyInput";

/**
 * Searchable package table with an Unlimited / FUP toggle.
 *
 * Shared by the Add Customer form and the Assign Plan dialog so the two can
 * never drift into offering different catalogues for the same decision.
 *
 * Base and Total are editable in the live CRM, so they are here too - but the
 * override is handed back to the caller rather than written to the Plan. A
 * plan is shared by every customer on it; editing the row in place would
 * reprice all of them.
 */
export default function PlanPicker({ value, onChange, overrides, onOverride }) {
  const [kind, setKind] = useState("unlimited");
  const [query, setQuery] = useState("");
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const timer = setTimeout(() => {
      get("/plans/picker", { kind, q: query })
        .then((response) => {
          if (cancelled) return;
          const rows = response?.data ?? response;
          setPlans(Array.isArray(rows) ? rows : []);
        })
        .catch((fetchError) => !cancelled && setError(fetchError))
        .finally(() => !cancelled && setLoading(false));
    }, query ? 300 : 0);   // debounce typing, but load the first page at once

    return () => { cancelled = true; clearTimeout(timer); };
  }, [kind, query]);

  const amountFor = (plan, key) => {
    const override = overrides?.[plan.id]?.[key];
    return override === undefined || override === null
      ? String(plan[key] ?? "") : override;
  };

  return (
    <div className="plan-choose">
      <div className="seg-toggle" role="tablist" aria-label="Plan family">
        {["unlimited", "fup"].map((option) => (
          <button key={option} type="button" role="tab"
                  aria-selected={kind === option}
                  className={kind === option ? "is-active" : ""}
                  onClick={() => setKind(option)}>
            {option === "fup" ? "FUP" : "Unlimited"}
          </button>
        ))}
      </div>

      <input className="plan-search" value={query} type="search"
             placeholder={`Search for ${kind}…`}
             onChange={(event) => setQuery(event.target.value)} />

      <div className="plan-picker">
        {loading ? <p className="muted padded">Loading packages…</p>
          : error ? <p className="field-error padded">
              The package list could not be loaded. Save the customer and
              assign a plan from their Plan tab.
            </p>
            : !plans.length ? <p className="muted padded">
                No {kind} packages match “{query}”.
              </p>
              : (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th aria-label="Select" />
                      <th>Package Name</th>
                      <th>Service Provider</th>
                      <th className="num">Base Amount</th>
                      <th className="num">Total Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plans.map((plan) => {
                      const chosen = String(plan.id) === String(value);
                      return (
                        <tr key={plan.id} className={chosen ? "is-chosen" : undefined}
                            onClick={() => onChange(String(plan.id))}>
                          <td>
                            <input type="radio" name="plan_id" value={plan.id}
                                   checked={chosen}
                                   onChange={() => onChange(String(plan.id))} />
                          </td>
                          <td>{plan.name}</td>
                          <td>{plan.service_provider || "—"}</td>
                          <td className="num">
                            <AmountCell disabled={!chosen}
                                        value={amountFor(plan, "base_amount")}
                                        onChange={(next) =>
                                          onOverride?.(plan.id, "base_amount", next)} />
                          </td>
                          <td className="num">
                            <AmountCell disabled={!chosen}
                                        value={amountFor(plan, "total_amount")}
                                        onChange={(next) =>
                                          onOverride?.(plan.id, "total_amount", next)} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
      </div>

      {value && plans.some((plan) => String(plan.id) === String(value)) && (
        <p className="hint">
          {(() => {
            const plan = plans.find((p) => String(p.id) === String(value));
            return `${plan.name} runs for ${plan.validity_days} days at `
              + `${inr(amountFor(plan, "total_amount"))}.`;
          })()}
        </p>
      )}
    </div>
  );
}

/** Amounts stay read-only until the row is the chosen one - editing the
    figures on a package you have not selected has no effect and only looks
    like it worked. */
function AmountCell({ value, onChange, disabled }) {
  return (
    <MoneyInput value={value} disabled={disabled}
                className="plan-amount"
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => onChange(event.target.value)} />
  );
}
