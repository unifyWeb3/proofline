window.PROOFLINE_VERIFIED_EXAMPLE = Object.freeze({
  job: "m4-source-proof-20260923234637-b465c5d2",
  transaction: "0x45943fce5c710c091f6c93c9c18549d548fea12a48a2b613fe66a05125bd2d36",
});

window.fetchProoflineVerifiedExample = async function fetchProoflineVerifiedExample() {
  const example = window.PROOFLINE_VERIFIED_EXAMPLE;
  let lastError = new Error("verified example unavailable");
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const query = new URLSearchParams({ tx: example.transaction, job: example.job });
      const response = await fetch(`/api/lifecycle?${query}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "verified example unavailable");
      if (!data.receipt || !data.finality_observed) throw new Error("verified example is not finalized");
      return data;
    } catch (error) {
      lastError = error;
      if (attempt === 0) await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
  }
  throw lastError;
};
