/* ── Insurance Automation — Frontend App ───────────────────────────────────── */

const API = ""; // Same origin

// ── State ────────────────────────────────────────────────────────────────────
let currentCaseId = null;
let pollTimer = null;
let logTimer = null;
let logLineCount = 0;

// ── DOM refs ─────────────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ── Toast ────────────────────────────────────────────────────────────────────
function toast(message, type = "info") {
	const container = $("#toast-container");
	const el = document.createElement("div");
	el.className = `toast ${type}`;
	el.textContent = message;
	container.appendChild(el);
	setTimeout(() => el.remove(), 4000);
}

// ── API helpers ──────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
	const res = await fetch(`${API}${path}`, opts);
	if (!res.ok) {
		const body = await res.json().catch(() => ({}));
		throw new Error(body.detail || `HTTP ${res.status}`);
	}
	return res.json();
}

// ── Navigation ───────────────────────────────────────────────────────────────
function showView(name) {
	$$(".view").forEach((v) => v.classList.remove("active"));
	$(`#view-${name}`).classList.add("active");
	$$(".nav-link").forEach((l) => l.classList.remove("active"));
	const link = $(`.nav-link[data-view="${name}"]`);
	if (link) link.classList.add("active");

	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
	if (logTimer) {
		clearInterval(logTimer);
		logTimer = null;
	}
}

$$(".nav-link").forEach((link) => {
	link.addEventListener("click", (e) => {
		e.preventDefault();
		const view = link.dataset.view;
		showView(view);
		if (view === "dashboard") loadCases();
		if (view === "settings") loadSettings();
	});
});

// ── Dashboard ────────────────────────────────────────────────────────────────
async function loadCases() {
	const grid = $("#cases-grid");
	try {
		const cases = await api("/api/cases");
		if (cases.length === 0) {
			grid.innerHTML = '<p class="empty-state">No cases yet. Click "New Case" to get started.</p>';
			return;
		}
		grid.innerHTML = cases
			.map(
				(c) => `
			<div class="folder-card status-${c.status}" data-id="${c.id}">
				<div class="folder-tab-row"><div class="folder-tab-knob"></div></div>
				<div class="folder-body">
					<div class="folder-body-top">
						<h3 class="folder-name">${esc(c.name)}</h3>
						<span class="status-badge status-${c.status}">${c.status}</span>
					</div>
					<div class="folder-meta">
						<span class="folder-meta-item">
							<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>
							${c.document_count} doc${c.document_count !== 1 ? "s" : ""}
						</span>
						<span class="folder-meta-item">
							<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
							${formatDate(c.created_at)}
						</span>
					</div>
				</div>
			</div>
		`,
			)
			.join("");

		grid.querySelectorAll(".folder-card").forEach((card) => {
			card.addEventListener("click", () => openCase(parseInt(card.dataset.id)));
		});
	} catch (err) {
		grid.innerHTML = `<p class="empty-state">Error loading cases: ${esc(err.message)}</p>`;
	}
}

// ── New Case Modal ───────────────────────────────────────────────────────────
const modal = $("#modal-new-case");

$("#btn-new-case").addEventListener("click", () => {
	$("#case-name").value = "";
	modal.classList.add("active");
	setTimeout(() => $("#case-name").focus(), 100);
});

$$(".modal-close").forEach((btn) => {
	btn.addEventListener("click", () => modal.classList.remove("active"));
});

modal.addEventListener("click", (e) => {
	if (e.target === modal) modal.classList.remove("active");
});

$("#case-name").addEventListener("keydown", (e) => {
	if (e.key === "Enter") $("#btn-create-case").click();
});

$("#btn-create-case").addEventListener("click", async () => {
	const name = $("#case-name").value.trim();
	if (!name) {
		toast("Please enter a case name", "error");
		return;
	}
	try {
		const fd = new FormData();
		fd.append("name", name);
		const c = await api("/api/cases", { method: "POST", body: fd });
		modal.classList.remove("active");
		toast(`Case "${c.name}" created`, "success");
		openCase(c.id);
	} catch (err) {
		toast(err.message, "error");
	}
});

// ── Open Case Detail ─────────────────────────────────────────────────────────
async function openCase(caseId) {
	if (caseId !== currentCaseId) {
		logLineCount = 0;
		const pre = $("#console-output");
		if (pre) pre.textContent = "";
		const consoleCard = $("#console-card");
		if (consoleCard) consoleCard.style.display = "none";
	}
	currentCaseId = caseId;
	showView("case");

	try {
		const c = await api(`/api/cases/${caseId}`);
		renderCase(c);

		if (c.status === "processing") {
			startPolling(caseId);
		} else {
			const pre = $("#console-output");
			if (pre && pre.textContent === "") {
				await fetchLogs(caseId, true);
			}
		}
	} catch (err) {
		toast(err.message, "error");
		showView("dashboard");
		loadCases();
	}
}

function renderCase(c) {
	$("#case-title").textContent = c.name;

	const badge = $("#case-status-badge");
	badge.textContent = c.status;
	badge.className = `status-badge status-${c.status}`;

	// ── Pipeline ──
	const pipeline = $("#pipeline");
	const isProcessing = c.status === "processing";
	const isDone = c.status === "completed";
	const isFailed = c.status === "failed";
	pipeline.style.display = isProcessing || isDone || isFailed ? "" : "none";

	// Reset pipeline steps
	$$(".pipeline-step").forEach((s) => s.classList.remove("active", "done"));
	$$(".pipeline-connector").forEach((c) => c.classList.remove("active"));

	if (isDone) {
		$$(".pipeline-step").forEach((s) => s.classList.add("done"));
		$$(".pipeline-connector").forEach((c) => c.classList.add("active"));
	} else if (isProcessing) {
		// Show classify as active initially; log parsing could refine this
		$('.pipeline-step[data-step="classify"]').classList.add("active");
	}

	// ── Upload card visibility ──
	const uploadCard = $("#upload-card");
	uploadCard.style.display = isProcessing ? "none" : "";

	// ── Uploaded Documents ──
	const uploadedList = $("#doc-uploaded-list");
	const uploadedCount = $("#uploaded-count");
	const docs = c.documents || [];
	uploadedCount.textContent = docs.length;
	if (docs.length > 0) {
		uploadedList.innerHTML = docs
			.map((d) => {
				const ext = extOf(d.original_name);
				const iconClass = ["jpg", "jpeg", "png"].includes(ext) ? "img" : "pdf";
				return `
				<div class="doc-tile doc-tile-clickable" data-url="/api/cases/${c.id}/documents/${d.id}">
					<div class="doc-tile-icon ${iconClass}">${getDocEmoji(ext)}</div>
					<div class="doc-tile-info">
						<div class="doc-tile-name" title="${esc(d.original_name)}">${esc(d.original_name)}</div>
						<div class="doc-tile-type">${ext.toUpperCase()} &mdash; click to open</div>
					</div>
				</div>`;
			})
			.join("");
		uploadedList.querySelectorAll(".doc-tile-clickable").forEach((tile) => {
			tile.addEventListener("click", () => window.open(tile.dataset.url, "_blank"));
		});
	} else {
		uploadedList.innerHTML = '<p style="color:var(--text-muted);font-size:13px;padding:8px 0">No documents uploaded yet</p>';
	}

	// ── Classified Documents ──
	const classifiedCard = $("#docs-classified-card");
	const classifiedList = $("#doc-classified-list");
	const classifiedCount = $("#classified-count");
	const classified = docs.filter((d) => d.classified_name);
	if (classified.length > 0) {
		classifiedCard.style.display = "";
		classifiedCount.textContent = classified.length;
		classifiedList.innerHTML = classified
			.map((d) => {
				const ext = extOf(d.classified_name);
				return `
				<div class="doc-tile doc-tile-clickable" data-url="/api/cases/${c.id}/classified/${encodeURIComponent(d.classified_name)}">
					<div class="doc-tile-icon classified">&#10003;</div>
					<div class="doc-tile-info">
						<div class="doc-tile-name" title="${esc(d.classified_name)}">${esc(d.classified_name)}</div>
						${d.doc_type ? `<span class="doc-tile-badge">${esc(d.doc_type)}</span>` : ""}
						<div class="doc-tile-type">click to open</div>
					</div>
				</div>`;
			})
			.join("");
		classifiedList.querySelectorAll(".doc-tile-clickable").forEach((tile) => {
			tile.addEventListener("click", () => window.open(tile.dataset.url, "_blank"));
		});

		// Update pipeline: if we have classified docs, classify step is done
		if (isProcessing) {
			$('.pipeline-step[data-step="classify"]').classList.remove("active");
			$('.pipeline-step[data-step="classify"]').classList.add("done");
			$$(".pipeline-connector")[0]?.classList.add("active");
			$('.pipeline-step[data-step="extract"]').classList.add("active");
		}
	} else {
		classifiedCard.style.display = "none";
	}

	// ── Process button ──
	const btnProcess = $("#btn-process");
	if (isProcessing) {
		btnProcess.disabled = true;
		btnProcess.innerHTML = `
			<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
			Processing...`;
	} else {
		btnProcess.disabled = docs.length === 0;
		btnProcess.innerHTML = `
			<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
			Start Processing`;
	}

	// ── Output files ──
	const outputCard = $("#output-card");
	const outputList = $("#output-list");
	if (c.output_files && c.output_files.length > 0) {
		outputCard.style.display = "";
		outputList.innerHTML = c.output_files
			.map(
				(f) => `
			<div class="output-item">
				<div class="output-item-name">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;flex-shrink:0"><path d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
					<span>${esc(f)}</span>
				</div>
				<a href="/api/cases/${c.id}/output/${encodeURIComponent(f)}" download>
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
					Download
				</a>
			</div>
		`,
			)
			.join("");
	} else {
		outputCard.style.display = "none";
	}

	// ── Error ──
	const errorCard = $("#error-card");
	if (c.error_message) {
		errorCard.style.display = "";
		$("#error-text").textContent = c.error_message;
	} else {
		errorCard.style.display = "none";
	}
}

// ── Polling & Logs ───────────────────────────────────────────────────────────
function startPolling(caseId) {
	if (pollTimer) clearInterval(pollTimer);
	if (logTimer) clearInterval(logTimer);

	pollTimer = setInterval(async () => {
		try {
			const c = await api(`/api/cases/${caseId}`);
			renderCase(c);
			// Only stop once we reach a terminal state — NOT on "created" which can
			// briefly appear before the background thread updates the status.
			if (c.status === "completed" || c.status === "failed") {
				clearInterval(pollTimer);
				pollTimer = null;
				clearInterval(logTimer);
				logTimer = null;
				await fetchLogs(caseId, true);
				if (c.status === "completed") toast("Processing completed!", "success");
				else if (c.status === "failed") toast("Processing failed. Check error details.", "error");
			}
		} catch {
			/* ignore */
		}
	}, 2000);

	logTimer = setInterval(() => fetchLogs(caseId, false), 1000);
}

async function fetchLogs(caseId, isFinal) {
	try {
		const data = await api(`/api/cases/${caseId}/logs?after=${logLineCount}`);
		if (data.lines && data.lines.length > 0) {
			appendLogs(data.lines);
			logLineCount += data.lines.length;
		}
		// Only mark the console as done when explicitly told this is the final fetch
		// (triggered by the poll detecting a terminal status). Relying on data.done
		// alone would fire too early because the server also returns done=true for the
		// initial "created" state before the processing thread starts.
		if (isFinal) {
			const statusEl = $("#console-status");
			statusEl.textContent = "Done";
			statusEl.className = "console-status done";
		}
	} catch {
		/* ignore */
	}
}

function appendLogs(lines) {
	const pre = $("#console-output");
	const consoleCard = $("#console-card");
	if (!pre) return;
	consoleCard.style.display = "";
	lines.forEach((line) => {
		pre.textContent += line + "\n";
	});
	pre.scrollTop = pre.scrollHeight;

	// Update pipeline based on log content
	updatePipelineFromLogs(lines);
}

function updatePipelineFromLogs(lines) {
	const text = lines.join("\n").toLowerCase();
	if (text.includes("extracting") || text.includes("extraction")) {
		$('.pipeline-step[data-step="classify"]')?.classList.replace("active", "done") || $('.pipeline-step[data-step="classify"]')?.classList.add("done");
		$('.pipeline-step[data-step="classify"]')?.classList.remove("active");
		$$(".pipeline-connector")[0]?.classList.add("active");
		$('.pipeline-step[data-step="extract"]')?.classList.add("active");
	}
	if (text.includes("filling") || text.includes("fill") || text.includes("excel") || text.includes("template")) {
		$('.pipeline-step[data-step="classify"]')?.classList.add("done");
		$('.pipeline-step[data-step="classify"]')?.classList.remove("active");
		$('.pipeline-step[data-step="extract"]')?.classList.add("done");
		$('.pipeline-step[data-step="extract"]')?.classList.remove("active");
		$$(".pipeline-connector")[0]?.classList.add("active");
		$$(".pipeline-connector")[1]?.classList.add("active");
		$('.pipeline-step[data-step="fill"]')?.classList.add("active");
	}
}

function resetConsole() {
	logLineCount = 0;
	const pre = $("#console-output");
	if (pre) pre.textContent = "";
	const statusEl = $("#console-status");
	if (statusEl) {
		statusEl.textContent = "Running...";
		statusEl.className = "console-status running";
	}
	const consoleCard = $("#console-card");
	if (consoleCard) consoleCard.style.display = "";
}

// ── Upload ───────────────────────────────────────────────────────────────────
const uploadZone = $("#upload-zone");
const fileInput = $("#file-input");

uploadZone.addEventListener("click", () => fileInput.click());
uploadZone.addEventListener("dragover", (e) => {
	e.preventDefault();
	uploadZone.classList.add("dragover");
});
uploadZone.addEventListener("dragleave", () => {
	uploadZone.classList.remove("dragover");
});
uploadZone.addEventListener("drop", (e) => {
	e.preventDefault();
	uploadZone.classList.remove("dragover");
	if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
	if (fileInput.files.length > 0) {
		uploadFiles(fileInput.files);
		fileInput.value = "";
	}
});

async function uploadFiles(files) {
	if (!currentCaseId) return;
	const fd = new FormData();
	for (const f of files) fd.append("files", f);
	try {
		await api(`/api/cases/${currentCaseId}/upload`, { method: "POST", body: fd });
		toast(`${files.length} file(s) uploaded`, "success");
		openCase(currentCaseId);
	} catch (err) {
		toast(err.message, "error");
	}
}

// ── Process ──────────────────────────────────────────────────────────────────
$("#btn-process").addEventListener("click", async () => {
	if (!currentCaseId) return;
	try {
		await api(`/api/cases/${currentCaseId}/process`, { method: "POST" });
		toast("Processing started...", "info");
		resetConsole();

		// Clear stale results from previous run immediately
		const outputCard = $("#output-card");
		if (outputCard) outputCard.style.display = "none";
		const errorCard = $("#error-card");
		if (errorCard) errorCard.style.display = "none";
		const classifiedCard = $("#docs-classified-card");
		if (classifiedCard) classifiedCard.style.display = "none";

		// Immediately show processing state — don't re-fetch status, the background
		// thread may not have updated it yet (race condition).
		const btnProcess = $("#btn-process");
		btnProcess.disabled = true;
		btnProcess.innerHTML = `
			<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
			Processing...`;

		const badge = $("#case-status-badge");
		badge.textContent = "processing";
		badge.className = "status-badge status-processing";

		const pipeline = $("#pipeline");
		if (pipeline) pipeline.style.display = "";
		$$(".pipeline-step").forEach((s) => s.classList.remove("active", "done"));
		$$(".pipeline-connector").forEach((c) => c.classList.remove("active"));
		$('.pipeline-step[data-step="classify"]')?.classList.add("active");

		// Hide upload card during processing
		const uploadCard = $("#upload-card");
		if (uploadCard) uploadCard.style.display = "none";

		// Start polling and log streaming immediately
		startPolling(currentCaseId);
	} catch (err) {
		toast(err.message, "error");
	}
});

// ── Delete Case ──────────────────────────────────────────────────────────────
$("#btn-delete-case").addEventListener("click", async () => {
	if (!currentCaseId) return;
	if (!confirm("Delete this case and all its files? This cannot be undone.")) return;
	try {
		await api(`/api/cases/${currentCaseId}`, { method: "DELETE" });
		toast("Case deleted", "success");
		currentCaseId = null;
		showView("dashboard");
		loadCases();
	} catch (err) {
		toast(err.message, "error");
	}
});

// ── Back Button ──────────────────────────────────────────────────────────────
$("#btn-back").addEventListener("click", () => {
	showView("dashboard");
	loadCases();
});

// ── Settings ─────────────────────────────────────────────────────────────────
async function loadSettings() {
	try {
		const settings = await api("/api/settings");
		$("#cases-folder").value = settings.cases_folder || "";
	} catch (err) {
		toast(err.message, "error");
	}
}

$("#btn-save-settings").addEventListener("click", async () => {
	const folder = $("#cases-folder").value.trim();
	if (!folder) {
		toast("Folder path is required", "error");
		return;
	}
	try {
		await api("/api/settings", {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ cases_folder: folder }),
		});
		toast("Settings saved", "success");
	} catch (err) {
		toast(err.message, "error");
	}
});

// ── Helpers ──────────────────────────────────────────────────────────────────
function esc(str) {
	const el = document.createElement("span");
	el.textContent = str || "";
	return el.innerHTML;
}

function extOf(filename) {
	if (!filename) return "";
	return filename.split(".").pop().toLowerCase();
}

function getDocEmoji(ext) {
	if (ext === "pdf") return "&#128459;";
	if (["jpg", "jpeg", "png"].includes(ext)) return "&#128247;";
	if (["xlsx", "xls"].includes(ext)) return "&#128200;";
	return "&#128196;";
}

function formatDate(iso) {
	if (!iso) return "";
	const d = new Date(iso);
	return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

// ── Init ─────────────────────────────────────────────────────────────────────
loadCases();
