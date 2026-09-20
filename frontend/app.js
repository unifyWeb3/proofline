(() => {
  const $ = (id) => document.getElementById(id);
  let account = null;
  let registrationHash = null;
  let submitHash = null;
  let pollTimer = null;
  let registrationTimer = null;
  let activeReceipt = null;
  let syncingEvidenceFields = false;

  const VERIFIED_EXAMPLE = {
    job: "browser-1789906301756",
    transaction: "0xa34586931cebe63f1392c3ea0233a21e406940d12157b78bc3ecec976be50c78",
  };

  const provider = () => window.ethereum;
  const JOB_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  const POLICY_KEYS = ["agreement_id", "deadline", "deterministic_requirements", "evidence_allowlist", "policy_version", "required_artifacts", "schema_version", "signer_address", "subjective_criterion"];
  const AGREEMENT_KEYS = ["agreement_id", "agreement_digest", "parties", "policy_version", "schema_version"];

  function setState(node, text, kind = "muted", detail = "") {
    node.className = `inline-state ${kind}`;
    node.replaceChildren();
    const marker = document.createElement("span");
    marker.className = "state-marker";
    marker.setAttribute("aria-hidden", "true");
    marker.textContent = kind === "error" ? "!" : kind === "finalized" || kind === "success" ? "✓" : kind === "rejected" ? "!" : "○";
    const message = document.createElement("span");
    message.textContent = text;
    node.append(marker, message);
    if (detail) {
      const note = document.createElement("small");
      note.textContent = detail;
      node.append(note);
    }
  }

  function setNetwork(text, success = true) {
    const node = $("network");
    node.replaceChildren();
    const dot = document.createElement("span");
    dot.className = `status-dot${success ? " status-dot-success" : ""}`;
    dot.setAttribute("aria-hidden", "true");
    node.append(dot, document.createTextNode(text));
  }

  async function config() {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) throw new Error("Proofline API is unavailable");
    return response.json();
  }

  async function connect() {
    if (!provider()) throw new Error("An injected wallet provider is required");
    const connectButton = $("connect");
    connectButton.disabled = true;
    connectButton.textContent = "Connecting…";
    try {
      const accounts = await provider().request({ method: "eth_requestAccounts" });
      account = accounts[0];
      if (!account) throw new Error("No wallet account was returned");
      const chain = await provider().request({ method: "eth_chainId" });
      const target = await config();
      const expected = `0x${target.chain_id.toString(16)}`;
      if (chain.toLowerCase() !== expected.toLowerCase()) throw new Error(`Wallet is on the wrong chain. Switch to Studio Next · ${target.chain_id}.`);
      await loadTemplates();
      connectButton.textContent = `${account.slice(0, 7)}…${account.slice(-4)}`;
      connectButton.title = account;
      setNetwork(`Wallet connected · Studio Next · ${target.chain_id}`, true);
      $("register").disabled = false;
      setState($("register-state"), "Wallet connected. Registration awaits authorization.", "success", "The next step will open your wallet for approval.");
    } catch (error) {
      connectButton.textContent = "Connect wallet";
      throw error;
    } finally {
      connectButton.disabled = false;
    }
  }

  const jsonValue = (id) => {
    try { return JSON.parse($(id).value); } catch (_) { throw new Error(`${id} must be valid JSON`); }
  };

  function sameKeys(value, required, optional = []) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const allowed = new Set([...required, ...optional]);
    return required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) && Object.keys(value).every((key) => allowed.has(key));
  }

  function validateInputs() {
    const jobId = $("job-id").value.trim();
    if (!JOB_ID_PATTERN.test(jobId)) throw new Error("Job ID must be a plain identifier matching ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$");
    const policy = jsonValue("policy");
    const agreement = jsonValue("agreement");
    if (!sameKeys(policy, POLICY_KEYS) || policy.schema_version !== "proofline.policy.v1") throw new Error("Policy must match proofline.policy.v1");
    if (!Number.isInteger(policy.deadline) || policy.deadline < 1 || !/^0x[0-9a-fA-F]{40}$/.test(policy.signer_address)) throw new Error("Policy deadline and signer_address are invalid");
    if (policy.agreement_id !== jobId || policy.required_artifacts?.length !== 1 || policy.required_artifacts[0] !== "response") throw new Error("Policy agreement_id and required_artifacts must match the registered job");
    if (!sameKeys(agreement, ["agreement_id", "parties", "policy_version", "schema_version"], ["agreement_digest"]) || agreement.schema_version !== "proofline.agreement.v1") throw new Error("Agreement must match proofline.agreement.v1");
    if (agreement.agreement_id !== jobId || agreement.policy_version !== policy.policy_version) throw new Error("Agreement identity and policy_version must match the registered job");
    if (agreement.agreement_digest !== undefined && !/^sha256:[0-9a-f]{64}$/.test(agreement.agreement_digest)) throw new Error("Agreement agreement_digest must be a sha256 digest");
    return { job_id: jobId, policy, agreement };
  }

  function populateEvidenceFields(raw) {
    try {
      const evidence = typeof raw === "string" ? JSON.parse(raw) : raw;
      $("request-question").value = evidence.request?.body?.question || evidence.request?.question || "";
      $("response-answer").value = evidence.response?.body?.answer || evidence.response?.answer || "";
      const references = evidence.response?.body?.evidence || evidence.response?.evidence || [];
      $("response-evidence").value = Array.isArray(references) ? references.join(", ") : "";
    } catch (_) {
      $("request-question").value = "";
      $("response-answer").value = "";
      $("response-evidence").value = "";
    }
  }

  function syncEvidenceFields() {
    if (syncingEvidenceFields) return;
    const question = $("request-question").value.trim();
    const answer = $("response-answer").value.trim();
    const evidence = $("response-evidence").value.split(",").map((value) => value.trim()).filter(Boolean);
    let current = {};
    try { current = JSON.parse($("evidence-content").value) || {}; } catch (_) { current = {}; }
    const next = {
      job_id: $("job-id").value.trim(),
      request: { ...(current.request || {}), method: current.request?.method || "POST", path: current.request?.path || "/v1/report", body: { ...(current.request?.body || {}), question } },
      response: { ...(current.response || {}), status: current.response?.status || 200, content_type: current.response?.content_type || "application/json", body: { ...(current.response?.body || {}), answer, evidence } },
    };
    $("evidence-content").value = JSON.stringify(next, null, 2);
  }

  async function loadTemplates() {
    const jobId = $("job-id").value.trim();
    const response = await fetch(`/api/templates?job=${encodeURIComponent(jobId)}&signer=${encodeURIComponent(account)}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Valid Proofline templates could not be loaded");
    $("policy").value = JSON.stringify(data.policy, null, 2);
    $("agreement").value = JSON.stringify(data.agreement, null, 2);
    syncingEvidenceFields = true;
    $("evidence-content").value = data.evidence_content;
    populateEvidenceFields(data.evidence_content);
    syncingEvidenceFields = false;
    setState($("envelope-state"), "Known-good semantic fixture loaded.", "muted", "Build the evidence package when the completed work is ready.");
  }

  async function prepare(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error([data.error, data.detail].filter(Boolean).join(": ") || "Transaction preparation failed");
    return data;
  }

  async function sendPrepared(prepared, stateNode) {
    setState(stateNode, "Awaiting wallet authorization…", "muted", "Review the unsigned transaction in your wallet.");
    const hash = await provider().request({ method: "eth_sendTransaction", params: [prepared.transaction] });
    setState(stateNode, `Submitted ${hash}`, "muted", "Waiting for provider inclusion before lifecycle observation.");
    return hash;
  }

  function setStepNav(step) {
    document.querySelectorAll("[data-step-nav]").forEach((node) => node.classList.toggle("active", node.dataset.stepNav === step));
  }

  async function register() {
    const button = $("register");
    try {
      button.disabled = true;
      const body = { from: account, ...validateInputs() };
      setState($("register-state"), "Preparing registration…", "muted", "Validating the policy and measured fee profile.");
      const prepared = await prepare("/api/prepare-register", body);
      registrationHash = await sendPrepared(prepared, $("register-state"));
      setState($("register-state"), `Registration submitted · ${registrationHash}`, "muted", "Registration finality is required before submission is enabled.");
      pollRegistration();
    } catch (error) {
      button.disabled = false;
      setState($("register-state"), error.message, "error", "No transaction was broadcast until preparation succeeded.");
    }
  }

  async function pollRegistration() {
    if (!registrationHash) return;
    clearTimeout(registrationTimer);
    try {
      const response = await fetch(`/api/transaction?tx=${encodeURIComponent(registrationHash)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Registration lifecycle query failed");
      if (data.state === "technical_error") {
        setState($("register-state"), `Registration failed · ${data.execution_result || data.protocol_status}`, "error", "The technical failure was not converted into a verdict.");
        buttonReady($("register"));
        return;
      }
      if (data.finality_observed) {
        setState($("register-state"), `Registration finalized · ${registrationHash}`, "finalized", "The job is now eligible for evidence submission.");
        $("submit").disabled = false;
        setState($("submit-state"), "Ready to submit", "success", "Registration reached authoritative finality. Wallet authorization is still required.");
        setStepNav("submit");
        return;
      }
      setState($("register-state"), `Registration pending · ${data.protocol_status || "processing"}`, "muted", "Waiting for authoritative registration finality.");
      registrationTimer = setTimeout(pollRegistration, 5000);
    } catch (error) {
      setState($("register-state"), error.message, "error", "The registration transaction remains under observation.");
      registrationTimer = setTimeout(pollRegistration, 8000);
    }
  }

  function buttonReady(button) {
    button.disabled = false;
  }

  async function submit() {
    try {
      const body = { from: account, ...validateInputs(), envelope: jsonValue("envelope"), evidence_content: $("evidence-content").value };
      if (body.policy.signer_address.toLowerCase() !== account.toLowerCase()) throw new Error("Submit wallet must match policy.signer_address");
      setState($("submit-state"), "Preparing submission…", "muted", "Validating the canonical evidence package and measured fee profile.");
      const prepared = await prepare("/api/prepare-submit", body);
      submitHash = await sendPrepared(prepared, $("submit-state"));
      setState($("submit-state"), `Submission submitted · ${submitHash}`, "muted", "Proofline is now observing the protocol lifecycle.");
      setStepNav("verify");
      updateTimeline({ transaction_hash: submitHash, state: "pending" });
      pollLifecycle();
    } catch (error) {
      setState($("submit-state"), error.message, "error", "No transaction was broadcast until preparation succeeded.");
    }
  }

  async function buildEnvelope() {
    try {
      setState($("envelope-state"), "Building evidence package…", "muted", "Canonicalizing the request, response, and registered policy.");
      const data = await prepare("/api/build-envelope", { ...validateInputs(), evidence_content: $("evidence-content").value });
      $("envelope").value = JSON.stringify(data.envelope, null, 2);
      $("evidence-digest").textContent = shortHash(data.evidence_digest);
      $("evidence-digest").title = data.evidence_digest;
      $("policy-digest").textContent = shortHash(data.policy_digest);
      $("policy-digest").title = data.policy_digest;
      $("agreement-digest").textContent = shortHash(data.agreement_digest);
      $("agreement-digest").title = data.agreement_digest;
      $("digest-list").classList.remove("hidden");
      $("envelope-details").classList.remove("hidden");
      setState($("envelope-state"), "Evidence package ready", "finalized", "Creates the canonical Proofline envelope and digests.");
    } catch (error) {
      setState($("envelope-state"), error.message, "error", "Fix the input before preparing a wallet transaction.");
    }
  }

  function timelineText(data, step) {
    if (step === "submitted") return data.transaction_hash ? "Transaction observed" : "Waiting for a transaction";
    if (step === "evaluating") return data.execution_result && data.execution_result !== "NOT_STARTED" ? formatLabel(data.execution_result) : "Waiting for evaluation";
    if (step === "consensus") return data.consensus_result ? formatLabel(data.consensus_result) : data.protocol_status ? formatLabel(data.protocol_status) : "Waiting for consensus";
    if (step === "finalized") return data.finality_observed ? "Authoritative result available" : "Waiting for finality";
    return "";
  }

  function updateTimeline(data) {
    const protocol = data.protocol_status || "";
    const hasEvaluation = ["ACCEPTED", "UNDETERMINED", "READY_TO_FINALIZE"].includes(protocol) || Boolean(data.execution_result && data.execution_result !== "NOT_STARTED" && data.execution_result !== "PENDING");
    const hasConsensus = ["PROPOSING", "COMMITTING", "REVEALING", "APPEAL_REVEALING", "APPEAL_COMMITTING"].includes(protocol) || Boolean(data.consensus_result);
    const final = Boolean(data.finality_observed);
    const progress = { submitted: Boolean(data.transaction_hash), evaluating: hasEvaluation, consensus: hasConsensus, finalized: final };
    const order = ["submitted", "evaluating", "consensus", "finalized"];
    document.querySelectorAll("[data-timeline]").forEach((node) => {
      const step = node.dataset.timeline;
      const index = order.indexOf(step);
      node.classList.toggle("complete", progress[step] && (step !== "finalized" || final));
      node.classList.toggle("active", progress[step] && !(step === "finalized" || progress[order[index + 1]]));
      node.classList.toggle("error", data.state === "technical_error" && (step === "evaluating" || step === "consensus"));
      const small = node.querySelector("small");
      if (small) small.textContent = timelineText(data, step);
    });
  }

  function lifecycleState(data) {
    if (data.state === "technical_error") return ["Technical error", "error", "No semantic verdict was produced. Inspect the provider detail and retry if appropriate."];
    if (data.finality_observed && data.receipt) return [`Finalized · ${formatLabel(data.protocol_status)}`, "finalized", `${formatLabel(data.execution_result)} · ${formatLabel(data.consensus_result)}`];
    if (data.state === "consensus") return ["Consensus in progress", "muted", `${formatLabel(data.protocol_status)} · waiting for an authoritative final state`];
    if (data.state === "evaluating") return ["Evaluation in progress", "muted", "Proofline is waiting for an authoritative finalized result."];
    return ["Submission observed", "muted", "Proofline is waiting for evaluation and finality."];
  }

  async function pollLifecycle() {
    if (!submitHash) return;
    clearTimeout(pollTimer);
    try {
      const job = encodeURIComponent($("job-id").value.trim());
      const response = await fetch(`/api/lifecycle?tx=${encodeURIComponent(submitHash)}&job=${job}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Lifecycle query failed");
      updateTimeline(data);
      const [message, kind, detail] = lifecycleState(data);
      setState($("lifecycle"), message, kind, detail);
      setReceiptStatus(data.receipt_verification || "pending");
      if (data.receipt) renderResult(data);
      if (!data.finality_observed && data.state !== "technical_error") pollTimer = setTimeout(pollLifecycle, 5000);
    } catch (error) {
      setState($("lifecycle"), error.message, "error", "The lifecycle query will be retried without creating a local verdict.");
      pollTimer = setTimeout(pollLifecycle, 8000);
    }
  }

  async function viewVerifiedExample() {
    const button = $("view-example");
    try {
      button.disabled = true;
      setStepNav("verify");
      $("stage-verify").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      setState($("lifecycle"), "Loading verified example…", "muted", "Reading the existing finalized job from Studio Next.");
      const response = await fetch(`/api/lifecycle?tx=${encodeURIComponent(VERIFIED_EXAMPLE.transaction)}&job=${encodeURIComponent(VERIFIED_EXAMPLE.job)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Verified example could not be loaded");
      if (!data.receipt || !data.finality_observed) throw new Error("Verified example is not finalized");
      $("example-note").classList.remove("hidden");
      updateTimeline(data);
      setReceiptStatus(data.receipt_verification || "pending");
      setState($("lifecycle"), `Finalized · ${formatLabel(data.protocol_status)}`, "finalized", "Existing verified example · no wallet transaction was made for this view.");
      renderResult(data);
    } catch (error) {
      setState($("lifecycle"), error.message, "error", "The example could not be loaded from authoritative state.");
    } finally {
      button.disabled = false;
    }
  }

  function renderResult(data) {
    const d = data.decision; const r = data.receipt; const a = data.adapter;
    activeReceipt = r;
    const verdict = d.verdict || "UNDETERMINED";
    $("result").classList.remove("hidden");
    $("result").innerHTML = `<div class="verdict-card ${verdict.toLowerCase()}"><div><span class="verdict-label">FINALIZED</span><h4>${escapeHtml(verdict)}</h4></div><div class="verdict-reason"><span>Reason</span><strong>${escapeHtml(formatLabel(d.reason_code))}</strong></div></div><dl class="facts"><div class="fact"><dt>Protocol state</dt><dd><span class="fact-value">${escapeHtml(r.protocol_status)}</span></dd></div><div class="fact"><dt>Execution</dt><dd><span class="fact-value">${escapeHtml(r.execution_result)}</span></dd></div><div class="fact"><dt>Consensus</dt><dd><span class="fact-value">${escapeHtml(data.consensus_result || "—")}</span></dd></div><div class="fact"><dt>Decision digest</dt><dd>${copyValue(r.decision_digest, true)}</dd></div><div class="fact"><dt>Finalized at</dt><dd><span class="fact-value mono" title="${escapeHtml(String(r.finalized_at))}">${escapeHtml(formatTimestamp(r.finalized_at))}</span></dd></div><div class="fact"><dt>Producing transaction</dt><dd>${copyValue(r.transaction_reference, true)}</dd></div><div class="fact"><dt>Receipt verification</dt><dd><span class="fact-value">${escapeHtml(String(data.receipt_verification).toUpperCase())}</span></dd></div><div class="fact"><dt>LocalAdapter</dt><dd><span class="fact-value">${escapeHtml(data.adapter_readiness || a?.signal || "—")}</span>${a?.receipt_digest ? copyValue(a.receipt_digest, true) : ""}</dd></div></dl>`;
    $("receipt-panel").classList.remove("hidden");
    $("receipt-json").textContent = JSON.stringify(r, null, 2);
  }

  function setReceiptStatus(value) {
    const normalized = String(value).toLowerCase();
    const label = normalized === "passed" ? "PASSED" : normalized === "failed" ? "FAILED" : "PENDING";
    const kind = label === "PASSED" ? "status-passed" : label === "FAILED" ? "status-failed" : "status-pending";
    $("receipt-status").className = `receipt-status status-pill ${kind}`;
    $("receipt-status").replaceChildren();
    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.setAttribute("aria-hidden", "true");
    $("receipt-status").append(dot, document.createTextNode(`Receipt verification: ${label}`));
  }

  function copyValue(value, mono = false) {
    const full = String(value || "—");
    return `<span class="fact-value${mono ? " mono" : ""}" title="${escapeHtml(full)}">${escapeHtml(shortHash(full))}</span><button class="copy-button fact-copy" type="button" data-copy-value="${escapeHtml(full)}">Copy</button>`;
  }

  async function copyText(value, button) {
    try {
      await navigator.clipboard.writeText(value);
      const original = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = original; }, 1200);
    } catch (_) {
      button.textContent = "Copy failed";
      window.setTimeout(() => { button.textContent = "Copy"; }, 1200);
    }
  }

  function downloadReceipt() {
    if (!activeReceipt) return;
    const blob = new Blob([JSON.stringify(activeReceipt, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${activeReceipt.agreement_id}-proofline-receipt.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function formatLabel(value) {
    if (!value) return "—";
    return String(value).replaceAll("_", " ").toLowerCase().replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
  }

  function formatTimestamp(value) {
    if (!value) return "—";
    const seconds = Number(value);
    if (!Number.isFinite(seconds)) return String(value);
    return `${seconds} · ${new Date(seconds * 1000).toLocaleString()}`;
  }

  function shortHash(value) {
    const text = String(value || "—");
    return text.length > 30 ? `${text.slice(0, 12)}…${text.slice(-12)}` : text;
  }

  function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])); }

  $("connect").addEventListener("click", () => connect().catch((error) => setState($("register-state"), error.message, "error", "No transaction was broadcast.")));
  $("register").addEventListener("click", register);
  $("build-envelope").addEventListener("click", buildEnvelope);
  $("submit").addEventListener("click", submit);
  $("view-example").addEventListener("click", viewVerifiedExample);
  $("download-receipt").addEventListener("click", downloadReceipt);
  ["request-question", "response-answer", "response-evidence"].forEach((id) => $(id).addEventListener("input", syncEvidenceFields));
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy-target], [data-copy-value]");
    if (!button) return;
    const target = button.dataset.copyTarget ? $(button.dataset.copyTarget) : null;
    const value = button.dataset.copyValue || target?.title || target?.textContent || "";
    copyText(value, button);
  });
  $("job-id").value = `browser-${Date.now()}`;
  config().then((data) => setNetwork(`Studio Next · ${data.chain_id}`, true)).catch((error) => setNetwork(error.message, false));
})();
