(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const form = $("bundle-form");
  const statusEl = $("status");
  const bundleOutput = $("bundle-output");
  const statFiles = $("stat-files");
  const statIgnored = $("stat-ignored");
  const statTokens = $("stat-tokens");
  const statMethod = $("stat-method");
  const ignoredBody = $("ignored-table").querySelector("tbody");
  const discoveredList = $("discovered-list");
  const warningsList = $("warnings-list");

  let lastResult = null;

  function setStatus(message, kind) {
    statusEl.textContent = message;
    statusEl.classList.remove("error", "success");
    if (kind === "error") statusEl.classList.add("error");
    if (kind === "success") statusEl.classList.add("success");
  }

  function renderResult(result) {
    lastResult = result;
    bundleOutput.value = result.bundle || "";
    statFiles.textContent = result.files ? result.files.length : 0;
    statIgnored.textContent = result.ignored ? result.ignored.length : 0;
    statTokens.textContent = result.token_count || 0;
    statMethod.textContent = result.token_method || "-";
    ignoredBody.innerHTML = "";
    (result.ignored || []).forEach((row) => {
      const tr = document.createElement("tr");
      const tdPath = document.createElement("td");
      tdPath.textContent = row.path;
      const tdReason = document.createElement("td");
      tdReason.textContent = row.reason;
      tr.appendChild(tdPath);
      tr.appendChild(tdReason);
      ignoredBody.appendChild(tr);
    });
    discoveredList.innerHTML = "";
    (result.discovered_ignore_files || []).forEach((path) => {
      const li = document.createElement("li");
      li.textContent = path;
      discoveredList.appendChild(li);
    });
    warningsList.innerHTML = "";
    (result.warnings || []).forEach((message) => {
      const li = document.createElement("li");
      li.textContent = message;
      warningsList.appendChild(li);
    });
  }

  async function postBundle(endpoint, body, options = {}) {
    setStatus("Bundling…");
    const response = await fetch(endpoint, {
      method: "POST",
      body,
      ...options,
    });
    if (!response.ok) {
      let errMessage = `Bundle failed (${response.status})`;
      try {
        const data = await response.json();
        if (data && data.error) errMessage = data.error;
      } catch (_) {
        // body was not JSON; keep generic message
      }
      throw new Error(errMessage);
    }
    return response;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      const response = await postBundle("/api/bundle", formData);
      const data = await response.json();
      renderResult(data);
      setStatus(
        `Bundled ${data.files.length} files (${data.token_count} tokens, ${data.token_method}).`,
        "success"
      );
    } catch (err) {
      setStatus(err.message, "error");
    }
  });

  async function triggerDownload(fmt) {
    try {
      const formData = new FormData(form);
      formData.append("format", fmt);
      const response = await postBundle("/api/bundle/download", formData);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download =
        fmt === "json" ? "bundle.json" : fmt === "zip" ? "bundle.zip" : "output.txt";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus(`Downloaded ${a.download}.`, "success");
    } catch (err) {
      setStatus(err.message, "error");
    }
  }

  $("download-text").addEventListener("click", () => triggerDownload("text"));
  $("download-zip").addEventListener("click", () => triggerDownload("zip"));
  $("download-json").addEventListener("click", () => triggerDownload("json"));

  $("copy-clipboard").addEventListener("click", async () => {
    if (!lastResult || !lastResult.bundle) {
      setStatus("Run a bundle first.", "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(lastResult.bundle);
      setStatus("Bundle copied to clipboard.", "success");
    } catch (err) {
      setStatus("Clipboard not available: " + err.message, "error");
    }
  });
})();
