window.PROOFLINE_VERIFIED_EXAMPLE = Object.freeze({
  job: "browser-1789906301756",
  transaction: "0xa34586931cebe63f1392c3ea0233a21e406940d12157b78bc3ecec976be50c78",
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
