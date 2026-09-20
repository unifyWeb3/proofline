(() => {
  const $ = (id) => document.getElementById(id);
  let account = null;
  let registrationHash = null;
  let submitHash = null;
  let pollTimer = null;
  let registrationTimer = null;
  let activeReceipt = null;

  const setState = (node, text, kind = "muted") => {
    node.textContent = text;
    node.className = `state ${kind}`;
  };

  const provider = () => window.ethereum;
  const JOB_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  const POLICY_KEYS = ["agreement_id", "deadline", "deterministic_requirements", "evidence_allowlist", "policy_version", "required_artifacts", "schema_version", "signer_address", "subjective_criterion"];
  const AGREEMENT_KEYS = ["agreement_id", "agreement_digest", "parties", "policy_version", "schema_version"];

  async function config() {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) throw new Error("Proofline API is unavailable");
    return response.json();
  }

  async function connect() {
    if (!provider()) throw new Error("A wallet provider is required");
    const accounts = await provider().request({ method: "eth_requestAccounts" });
    account = accounts[0];
    const chain = await provider().request({ method: "eth_chainId" });
    const expected = `0x${(await config()).chain_id.toString(16)}`;
    if (chain.toLowerCase() !== expected.toLowerCase()) throw new Error("Wallet is on the wrong chain");
    await loadTemplates();
    $("network").textContent = `Wallet connected · ${account.slice(0, 8)}…${account.slice(-6)} · chain ${parseInt(chain, 16)}`;
    $("register").disabled = false;
    setState($("register-state"), "Wallet connected. Registration awaits authorization.");
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

  async function loadTemplates() {
    const jobId = $("job-id").value.trim();
    const response = await fetch(`/api/templates?job=${encodeURIComponent(jobId)}&signer=${encodeURIComponent(account)}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Valid Proofline templates could not be loaded");
    $("policy").value = JSON.stringify(data.policy, null, 2);
    $("agreement").value = JSON.stringify(data.agreement, null, 2);
    $("evidence-content").value = data.evidence_content;
    setState($("envelope-state"), "Known-good Milestone 2 semantic fixture loaded. Build its matching envelope.");
  }

  async function prepare(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error([data.error, data.detail].filter(Boolean).join(": ") || "Transaction preparation failed");
    return data;
  }

  async function sendPrepared(prepared, stateNode) {
    setState(stateNode, "Awaiting wallet authorization…");
    const hash = await provider().request({ method: "eth_sendTransaction", params: [prepared.transaction] });
    setState(stateNode, `Submitted ${hash}. Waiting for provider inclusion…`);
    return hash;
  }

  async function register() {
    try {
      const body = { from: account, ...validateInputs() };
      const prepared = await prepare("/api/prepare-register", body);
      registrationHash = await sendPrepared(prepared, $("register-state"));
      setState($("register-state"), `Registration submitted · ${registrationHash}`);
      pollRegistration();
    } catch (error) { setState($("register-state"), error.message, "error"); }
  }

  async function pollRegistration() {
    if (!registrationHash) return;
    clearTimeout(registrationTimer);
    try {
      const response = await fetch(`/api/transaction?tx=${encodeURIComponent(registrationHash)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Registration lifecycle query failed");
      if (data.state === "technical_error") {
        setState($("register-state"), `Registration failed · ${data.execution_result || data.protocol_status}`, "error");
        return;
      }
      if (data.finality_observed) {
        setState($("register-state"), `Registration finalized · ${registrationHash}`, "finalized");
        $("submit").disabled = false;
        setState($("submit-state"), "Registration finalized. Submission awaits authorization.");
        return;
      }
      setState($("register-state"), `Registration pending · ${data.protocol_status || "processing"}`);
      registrationTimer = setTimeout(pollRegistration, 5000);
    } catch (error) {
      setState($("register-state"), error.message, "error");
      registrationTimer = setTimeout(pollRegistration, 8000);
    }
  }

  async function submit() {
    try {
      const body = { from: account, ...validateInputs(), envelope: jsonValue("envelope"), evidence_content: $("evidence-content").value };
      if (body.policy.signer_address.toLowerCase() !== account.toLowerCase()) throw new Error("Submit wallet must match policy.signer_address");
      const prepared = await prepare("/api/prepare-submit", body);
      submitHash = await sendPrepared(prepared, $("submit-state"));
      setState($("submit-state"), `Submission submitted · ${submitHash}`);
      pollLifecycle();
    } catch (error) { setState($("submit-state"), error.message, "error"); }
  }

  async function buildEnvelope() {
    try {
      const data = await prepare("/api/build-envelope", {
        ...validateInputs(),
        evidence_content: $("evidence-content").value,
      });
      $("envelope").value = JSON.stringify(data.envelope, null, 2);
      setState($("envelope-state"), `Envelope built from canonical evidence · ${data.evidence_digest}` , "finalized");
    } catch (error) { setState($("envelope-state"), error.message, "error"); }
  }

  async function pollLifecycle() {
    if (!submitHash) return;
    clearTimeout(pollTimer);
    try {
      const job = encodeURIComponent($("job-id").value.trim());
      const response = await fetch(`/api/lifecycle?tx=${encodeURIComponent(submitHash)}&job=${job}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Lifecycle query failed");
      setReceiptStatus(data.receipt_verification || "pending");
      const kind = data.state === "finalized" ? "finalized" : data.state === "semantic_rejection" ? "rejected" : data.state === "technical_error" ? "error" : "muted";
      setState($("lifecycle"), `${data.state} · protocol ${data.protocol_status || "unknown"} · execution ${data.execution_result || "pending"}`, kind);
      if (data.receipt) renderResult(data);
      if (!data.finality_observed && data.state !== "technical_error") pollTimer = setTimeout(pollLifecycle, 5000);
    } catch (error) {
      setState($("lifecycle"), error.message, "error");
      pollTimer = setTimeout(pollLifecycle, 8000);
    }
  }

  function renderResult(data) {
    const d = data.decision; const r = data.receipt; const a = data.adapter;
    activeReceipt = r;
    $("result").classList.remove("hidden");
    $("result").innerHTML = `<dl class="facts"><dt>Job</dt><dd>${escapeHtml(d.agreement_id)}</dd><dt>Verdict</dt><dd>${escapeHtml(d.verdict)} · ${escapeHtml(d.reason_code)}</dd><dt>Decision digest</dt><dd>${escapeHtml(r.decision_digest)}</dd><dt>Finalized at</dt><dd>${escapeHtml(String(r.finalized_at))}</dd><dt>Producing transaction</dt><dd>${escapeHtml(r.transaction_reference)}</dd><dt>Receipt verification</dt><dd>${escapeHtml(data.receipt_verification)}</dd><dt>LocalAdapter</dt><dd>${escapeHtml(data.adapter_readiness)} · ${escapeHtml(a.receipt_digest)}</dd></dl>`;
    $("receipt-panel").classList.remove("hidden");
    $("receipt-json").textContent = JSON.stringify(r, null, 2);
  }

  function setReceiptStatus(value) {
    const normalized = String(value).toLowerCase();
    const label = normalized === "passed" ? "PASSED" : normalized === "failed" ? "FAILED" : "PENDING";
    const kind = label === "PASSED" ? "finalized" : label === "FAILED" ? "error" : "muted";
    setState($("receipt-status"), `Receipt verification: ${label}`, kind);
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

  function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }

  $("connect").addEventListener("click", () => connect().catch((error) => setState($("register-state"), error.message, "error")));
  $("register").addEventListener("click", register);
  $("build-envelope").addEventListener("click", buildEnvelope);
  $("submit").addEventListener("click", submit);
  $("download-receipt").addEventListener("click", downloadReceipt);
  $("job-id").value = `browser-${Date.now()}`;
  config().then((c) => { $("network").textContent = `Studio Next · chain ${c.chain_id} · contract ${c.contract_address}`; }).catch((error) => { $("network").textContent = error.message; });
})();
