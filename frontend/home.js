(() => {
  const $ = (id) => document.getElementById(id);
  const example = window.PROOFLINE_VERIFIED_EXAMPLE;

  function label(value) {
    return value ? String(value).replaceAll("_", " ").toLowerCase().replace(/(^|\s)\S/g, (letter) => letter.toUpperCase()) : "—";
  }

  function setExample(data) {
    const decision = data.decision || {};
    const receipt = data.receipt || {};
    const receiptStatus = String(data.receipt_verification || "pending").toUpperCase();
    $("home-job").textContent = example.job;
    $("home-chain-job").textContent = example.job;
    $("home-example-status").textContent = "Authoritative result verified";
    $("home-final-state").textContent = label(receipt.protocol_status || data.protocol_status).toUpperCase();
    $("home-verdict").textContent = decision.verdict || receipt.verdict || "—";
    $("home-reason").textContent = label(decision.reason_code || receipt.reason_code);
    $("home-receipt").textContent = receiptStatus;
    $("home-consensus").textContent = label(data.consensus_result);
    $("home-execution").textContent = label(data.execution_result);
    $("home-adapter").textContent = data.adapter_readiness || data.adapter?.signal || "—";
    $("home-chain-verdict").textContent = decision.verdict || receipt.verdict || "—";
    $("home-chain-state").textContent = label(receipt.protocol_status || data.protocol_status);
    $("home-chain-receipt").textContent = receiptStatus;
    $("home-chain-adapter").textContent = data.adapter_readiness || data.adapter?.signal || "—";
  }

  async function loadExample() {
    try {
      const data = await window.fetchProoflineVerifiedExample();
      setExample(data);
    } catch (_) {
      $("home-example-status").textContent = "Example readback unavailable";
      $("home-final-state").textContent = "—";
      $("home-reason").textContent = "No finalized result loaded";
    }
  }

  loadExample();
})();
