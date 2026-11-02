(() => {
  const savedKey = "jobScoutSavedIndia";
  const passwordKey = "pw";

  function savedIds() {
    try {
      const value = JSON.parse(localStorage.getItem(savedKey) || "[]");
      return Array.isArray(value) ? value.filter(id => typeof id === "string") : [];
    } catch {
      return [];
    }
  }

  async function api(url, options = {}) {
    let password = localStorage.getItem(passwordKey);
    if (!password) {
      password = window.prompt("Enter your Job Scout password");
      if (!password) throw new Error("Password is required to continue.");
      localStorage.setItem(passwordKey, password);
    }
    const response = await fetch(url, {
      ...options,
      headers: { ...(options.headers || {}), "x-pass": password, "Content-Type": "application/json" }
    });
    if (response.status === 401) {
      localStorage.removeItem(passwordKey);
      throw new Error("Incorrect password. Refresh the page and try again.");
    }
    if (!response.ok) throw new Error(`Request failed (${response.status}). Please try again.`);
    return response.json();
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function isSaved(id) {
    return savedIds().includes(id);
  }

  function toggleSaved(id) {
    const ids = savedIds();
    const next = ids.includes(id) ? ids.filter(item => item !== id) : [...ids, id];
    localStorage.setItem(savedKey, JSON.stringify(next));
    return next.includes(id);
  }

  function jobCard(job, options = {}) {
    const saved = isSaved(job._id);
    const when = job.seen ? new Date(job.seen * 1000).toLocaleDateString() : "";
    return `<article class="job" data-job-id="${escapeHtml(job._id)}">
      <h2>${escapeHtml(job.title)}</h2>
      <p class="job-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || "India")}</p>
      <p class="muted">${escapeHtml(job.work_modes?.join(" · ") || "India-based")} · ${when ? `Added ${when}` : ""}</p>
      <div class="job-actions">
        <a class="button secondary" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">View opening ↗</a>
        <a class="button primary" href="/applications.html?job=${encodeURIComponent(job._id)}">Tailor application</a>
        <button class="secondary" type="button" data-save-id="${escapeHtml(job._id)}">${saved ? "Saved ✓" : "Save job"}</button>
        ${options.remove ? `<button class="danger" type="button" data-remove-id="${escapeHtml(job._id)}">Remove</button>` : ""}
      </div>
    </article>`;
  }

  document.addEventListener("click", event => {
    const target = event.target.closest("[data-save-id], [data-remove-id]");
    if (!target) return;
    const id = target.dataset.saveId || target.dataset.removeId;
    if (!id) return;
    toggleSaved(id);
    if (target.dataset.removeId) {
      target.closest(".job").remove();
      const container = document.getElementById("jobs");
      if (container && !container.querySelector(".job")) {
        container.innerHTML = '<div class="empty">No saved jobs yet. Save roles from the Jobs page to keep them here.</div>';
      }
    } else {
      target.textContent = isSaved(id) ? "Saved ✓" : "Save job";
    }
    window.dispatchEvent(new CustomEvent("job-saved", { detail: { id, saved: isSaved(id) } }));
  });

  window.JobScout = { api, escapeHtml, isSaved, savedIds, jobCard, toggleSaved };
})();
