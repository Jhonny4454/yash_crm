import { useEffect, useState } from "react";
import { post, put } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useToast } from "../context/ToastContext";
import { readableError } from "./ui";
import "../styles/WhatsAppTester.css";

/**
 * Configure the WhatsApp gateway, then prove it works — in one place.
 *
 * Both halves exist because of the same failure. Until now, sending was
 * switched on by finding `wa_enabled`, `wa_provider`, `wa_api_token` and
 * `wa_api_url` among a hundred alphabetically-sorted raw setting rows, knowing
 * that the first had to be exactly "1", and then discovering whether any of it
 * worked by raising a real bill for a real customer. And when it was NOT
 * configured, the app logged the message and reported success - so "nothing
 * arrives" looked exactly like "working fine".
 */
export default function WhatsAppTester() {
  const { toast } = useToast();
  const { data: status, refetch } = useFetch("/settings/whatsapp/status");

  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [mobile, setMobile] = useState("");
  // "" = free text. Anything else sends that APPROVED TEMPLATE instead.
  const [testTemplate, setTestTemplate] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [repairing, setRepairing] = useState(false);

  // Seed the form once the server has told us the current state. Keyed off
  // `status` rather than initialised empty, or the fields would flash blank
  // and an operator could save that blank over a working configuration.
  useEffect(() => {
    if (!status || form) return;
    setForm({
      enabled: Boolean(status.enabled),
      provider: status.provider || "generic",
      api_url: status.api_url || "",
      api_token: "",
      country_code: status.country_code || "91",
      instance_id: status.instance_id || "",
    });
  }, [status, form]);

  const providers = status?.providers || [];
  const chosen = providers.find((p) => p.id === form?.provider);
  const builtIn = chosen?.endpoint || "";

  /* Has the form been edited without saving?
   *
   * This matters more than it looks. The form and the test button sit next to
   * each other, but the test runs SERVER-side against the STORED settings - so
   * filling in the provider and key and pressing Send test ran the old, empty
   * configuration and reported "WhatsApp is switched off", with every field on
   * screen visibly filled in. The test is blocked while there are unsaved
   * changes rather than quietly testing something else. */
  const dirty = Boolean(form && status && (
    form.enabled !== Boolean(status.enabled)
    || form.provider !== (status.provider || "generic")
    || form.api_url !== (status.api_url || "")
    || form.country_code !== (status.country_code || "91")
    || form.instance_id !== (status.instance_id || "")
    || form.api_token.length > 0));

  async function save(event) {
    event.preventDefault();
    if (saving || !form) return;
    setSaving(true);
    try {
      const payload = await put("/settings/whatsapp", form);
      const data = payload?.data ?? payload;
      toast.success(data.ready
        ? "Saved. The gateway is ready — send a test to confirm."
        : "Saved, but sending is not ready yet. See what is missing above.");
      setForm((current) => ({ ...current, api_token: "" }));
      refetch();
    } catch (err) {
      toast.error(err.detail || readableError(err));
    } finally {
      setSaving(false);
    }
  }

  async function runTest(event) {
    event.preventDefault();
    if (busy || !mobile.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const payload = await post("/settings/whatsapp/test",
        { mobile: mobile.trim(), template_type: testTemplate || undefined });
      setResult(payload?.data ?? payload);
      refetch();
    } catch (err) {
      setResult({ status: "failed", detail: err.detail || readableError(err) });
    } finally {
      setBusy(false);
    }
  }

  /* Repair the message templates.
   *
   * Sending a bill fails with "no active template" when the row is missing,
   * switched off, or has an empty body - three states that look identical
   * from the outside. This fixes all three.
   *
   * It lives HERE rather than on the Notifications screen, which edits a
   * different table entirely (notification_templates, for in-app notices).
   * Putting the button there meant it repaired one thing while reporting on
   * another. A missing template is a sending problem, and this is the panel
   * that answers "can we send?". */
  async function repairTemplates() {
    if (repairing) return;
    setRepairing(true);
    try {
      const payload = await post("/notifications/templates/restore-defaults");
      const data = payload?.data ?? payload;
      toast.success(data.changed
        ? `${data.detail} (${[
          data.created.length && `${data.created.length} created`,
          data.reactivated.length && `${data.reactivated.length} switched on`,
          data.refilled.length && `${data.refilled.length} refilled`,
        ].filter(Boolean).join(", ")})`
        : "Every standard template is already present and active.");
      refetch();
    } catch (err) {
      toast.error(err.detail || readableError(err));
    } finally {
      setRepairing(false);
    }
  }

  const templates = status?.templates;

  /* "queued" is amber, not green.
   *
   * The gateway answering {"status":"QUEUED","success":true} is it taking
   * custody of the message, not WhatsApp delivering it. Painting that green
   * and calling it sent is what sent us hunting for a bug in the CRM while
   * the message sat undelivered at Meta. */
  const tone = result?.status === "sent" ? "ok"
    : ["warning", "queued", "not_configured"].includes(result?.status) ? "warn"
      : "bad";

  return (
    <section className="panel wa-tester">
      <h2>WhatsApp gateway</h2>

      <dl className="wa-status">
        <div><dt>Sending</dt><dd>{status?.enabled ? "On" : "Off"}</dd></div>
        <div><dt>Provider</dt><dd>{status?.provider || "—"}</dd></div>
        <div>
          <dt>API key</dt>
          {/* The stored key's PREFIX, redacted. "Set" was not enough: a
              WabAssist Key ID (key_...) is set, looks fine, and can never
              authenticate - the credential begins ua_. */}
          <dd className={status?.api_key_looks_wrong ? "wa-key-bad" : undefined}>
            {status?.has_api_key ? (status.api_key_hint || "Set") : "Not set"}
          </dd>
        </div>
        <div className="wa-status-wide">
          <dt>Endpoint</dt>
          <dd className="wa-endpoint">{status?.endpoint || "Not set"}</dd>
        </div>
      </dl>

      {status && !status.ready && (
        <div className="alert error" role="alert">
          <div>
            <strong>Not ready to send.</strong> Messages are written to the
            message log instead of being delivered.
            <ul>
              {(status.blocking || []).map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </div>
        </div>
      )}

      {/* Separate from "not ready": this gateway sends perfectly well to
          anyone who has just messaged you, which is exactly why the problem
          survives a successful test. It is the bills that never arrive. */}
      {(status?.warnings || []).length > 0 && (
        <div className="alert warn" role="status">
          <div>
            <strong>Bills and reminders will not reach most customers.</strong>
            <ul>
              {status.warnings.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
        </div>
      )}

      {status?.api_key_looks_wrong && (
        <div className="alert error" role="alert">
          <div>
            <strong>That is the Key ID, not the API key.</strong> WabAssist
            authenticates with the value beginning <code>ua_</code> — their docs
            say “Use saved key (ua_…) for auth”. The one stored here starts{" "}
            <code>key_</code> and will always be refused. Generate a new key on
            their API Docs page and paste the <code>ua_…</code> value below.
          </div>
        </div>
      )}

      {form && (
        <form className="wa-config" onSubmit={save}>
          <label className="wa-check">
            <input type="checkbox" checked={form.enabled}
                   onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
            <span>Send messages for real</span>
          </label>

          <div className="wa-config-grid">
            <label>
              Provider
              <select value={form.provider}
                      onChange={(e) => setForm({ ...form, provider: e.target.value })}>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </label>

            <label>
              API key
              <input type="password" autoComplete="off" value={form.api_token}
                     placeholder={status?.has_api_key ? "•••••• (unchanged)" : "Paste your key"}
                     onChange={(e) => setForm({ ...form, api_token: e.target.value })} />
            </label>

            <label>
              Country code
              <input value={form.country_code} inputMode="numeric"
                     onChange={(e) => setForm({ ...form, country_code: e.target.value })} />
            </label>

            {/* Meta puts the phone number id in the URL path, so it is
                configuration rather than a credential - and it is not the
                phone NUMBER, which is the mistake everyone makes first. */}
            {chosen?.needs_instance && (
              <label>
                Phone Number ID
                <input value={form.instance_id} inputMode="numeric"
                       placeholder="e.g. 123456789012345"
                       onChange={(e) => setForm({ ...form, instance_id: e.target.value })} />
                <small>
                  Meta Business &gt; WhatsApp &gt; API Setup. A long number —
                  not the phone number itself.
                </small>
              </label>
            )}

            <label className="wa-config-wide">
              API URL
              <input value={form.api_url} inputMode="url"
                     placeholder={builtIn
                       || (chosen?.needs_instance
                         ? "Built from the Phone Number ID"
                         : "Required for this provider")}
                     onChange={(e) => setForm({ ...form, api_url: e.target.value })} />
              <small>
                {builtIn
                  ? `Leave blank to use ${builtIn}. Set it only if your gateway told you a different address.`
                  : chosen?.needs_instance
                    ? "Leave blank. It is built from the Phone Number ID above."
                    : "This provider has no built-in address, so a URL is required."}
              </small>
            </label>
          </div>

          {chosen?.note && <p className="wa-provider-note">{chosen.note}</p>}

          <button className="btn primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : dirty ? "Save gateway settings" : "Saved"}
          </button>
          {/* Blank means "leave the stored key alone", so re-saving the form
              cannot wipe a key the operator can no longer see. */}
        </form>
      )}

      {templates && (
        <div className={`wa-templates ${templates.bill_ready ? "is-ok" : "is-bad"}`}>
          <div>
            <strong>Message templates</strong>
            <p>
              {templates.bill_ready
                ? `${templates.usable.length} usable.`
                : "No usable bill template — sending a bill will fail for every customer."}
              {templates.missing.length > 0
                && ` Missing: ${templates.missing.join(", ")}.`}
              {templates.broken.length > 0
                && ` Broken: ${templates.broken
                  .map((b) => `${b.type} (${b.why})`).join(", ")}.`}
            </p>
          </div>
          {templates.needs_repair && (
            <button type="button" className="btn sm primary" disabled={repairing}
                    onClick={repairTemplates}>
              {repairing ? "Restoring…" : "Restore defaults"}
            </button>
          )}
        </div>
      )}

      <form className="wa-test-form" onSubmit={runTest}>
        <label htmlFor="wa-test-mobile">Send a test message to</label>
        <div className="wa-test-row">
          <input id="wa-test-mobile" className="input" value={mobile} inputMode="tel"
                 placeholder="98765 43210"
                 onChange={(event) => setMobile(event.target.value)} />
          <select className="input" style={{ maxWidth: 230 }} value={testTemplate}
                  aria-label="What to send"
                  onChange={(event) => setTestTemplate(event.target.value)}>
            <option value="">Free text</option>
            {/* Labelled by the CRM's own message type first, because several
                of them map to the SAME approved template - four rows all read
                "invoice_attachment" otherwise and there is no way to tell
                which one you are testing. */}
            {(status?.templates?.linked || []).map((item) => (
              <option key={item.template_type} value={item.template_type}>
                {item.template_type} → {item.meta_template_name}
                {item.needs_pdf ? " (needs PDF link)" : ""}
              </option>
            ))}
          </select>
          <button className="btn primary" disabled={busy || dirty || !mobile.trim()}>
            {busy ? "Sending…" : "Send test"}
          </button>
        </div>
        <small className={dirty ? "wa-dirty" : undefined}>
          {dirty
            ? "Save the gateway settings above first — the test uses the saved "
              + "configuration, not what is typed on screen."
            : testTemplate
              ? "Sends the approved template. This is the one worth trusting: "
                + "send it to a number that has NOT messaged you today, because "
                + "that is every customer a bill goes to."
              : "Free text only reaches someone who messaged you in the last 24 "
                + "hours — so it proves the gateway answers, not that bills "
                + "arrive. Pick a template above to test that."}
        </small>
      </form>

      {result && (
        <div className={`wa-result is-${tone}`} role="status">
          <strong>
            {result.status === "sent" ? "The gateway accepted the message"
              : result.status === "queued"
                ? "Queued at the gateway — delivery is not confirmed"
                : result.status === "not_configured" ? "Nothing was sent"
                  : result.status === "invalid_number" ? "That number was not usable"
                    : "The gateway refused it"}
          </strong>
          <p>{result.detail}</p>
          {result.hint && <p className="wa-hint">{result.hint}</p>}

          {/* The raw exchange. An operator on the phone to their gateway's
              support desk needs the URL, the body and the status code. The
              API key is redacted server-side. */}
          {(result.request || result.response) && (
            <details>
              <summary>What was sent and what came back</summary>
              {result.request && (
                <pre>{result.request.method} {result.request.url}
{result.request.auth ? `Authorization: ${result.request.auth}\n` : ""}{result.request.body ? JSON.stringify(result.request.body, null, 2) : "(no body)"}</pre>
              )}
              {result.response && (
                <pre>HTTP {result.response.http_status}
{result.response.body || "(empty response)"}</pre>
              )}
            </details>
          )}
        </div>
      )}
    </section>
  );
}
