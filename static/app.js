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
				const showDelete = !isProcessing;
				return `
				<div class="doc-tile" data-doc-id="${d.id}">
					<div class="doc-tile-icon ${iconClass}" data-url="/api/cases/${c.id}/documents/${d.id}" style="cursor:pointer">${getDocEmoji(ext)}</div>
					<div class="doc-tile-info doc-tile-clickable" data-url="/api/cases/${c.id}/documents/${d.id}" style="cursor:pointer">
						<div class="doc-tile-name" title="${esc(d.original_name)}">${esc(d.original_name)}</div>
						<div class="doc-tile-type">${ext.toUpperCase()} &mdash; click to open</div>
					</div>
					${
						showDelete
							? `<button class="btn-delete-doc" data-doc-id="${d.id}" title="Delete document">
						<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
					</button>`
							: ""
					}
				</div>`;
			})
			.join("");
		uploadedList.querySelectorAll(".doc-tile-clickable, .doc-tile-icon[data-url]").forEach((el) => {
			el.addEventListener("click", () => window.open(el.dataset.url, "_blank"));
		});
		uploadedList.querySelectorAll(".btn-delete-doc").forEach((btn) => {
			btn.addEventListener("click", (e) => {
				e.stopPropagation();
				deleteDocument(c.id, parseInt(btn.dataset.docId));
			});
		});
	} else {
		uploadedList.innerHTML = '<p style="color:var(--text-muted);font-size:13px;padding:8px 0">No documents uploaded yet</p>';
	}

	// ── Classified Documents ──
	const classifiedCard = $("#docs-classified-card");
	const classifiedList = $("#doc-classified-list");
	const classifiedCount = $("#classified-count");
	const classified = docs.filter((d) => d.classified_name);
	// Deduplicate by classified_name (multiple source docs may merge into one classified file)
	const seenClassified = new Set();
	const uniqueClassified = classified.filter((d) => {
		if (seenClassified.has(d.classified_name)) return false;
		seenClassified.add(d.classified_name);
		return true;
	});
	if (uniqueClassified.length > 0) {
		classifiedCard.style.display = "";
		classifiedCount.textContent = uniqueClassified.length;

		// Set ZIP download link
		const btnZip = $("#btn-download-classified-zip");
		if (btnZip) {
			btnZip.href = `/api/cases/${c.id}/classified/download/zip`;
			btnZip.setAttribute("download", "");
		}

		classifiedList.innerHTML = uniqueClassified
			.map((d) => {
				const ext = extOf(d.classified_name);
				const showDelete = !isProcessing;
				return `
				<div class="doc-tile" data-doc-id="${d.id}">
					<div class="doc-tile-icon classified" data-url="/api/cases/${c.id}/classified/${encodeURIComponent(d.classified_name)}" style="cursor:pointer">&#10003;</div>
					<div class="doc-tile-info doc-tile-clickable" data-url="/api/cases/${c.id}/classified/${encodeURIComponent(d.classified_name)}" style="cursor:pointer">
						<div class="doc-tile-name" title="${esc(d.classified_name)}">${esc(d.classified_name)}</div>
						${d.doc_type ? `<span class="doc-tile-badge">${esc(d.doc_type)}</span>` : ""}
						<div class="doc-tile-type">click to open</div>
					</div>
					${
						showDelete
							? `<button class="btn-delete-doc" data-doc-id="${d.id}" title="Delete document">
						<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
					</button>`
							: ""
					}
				</div>`;
			})
			.join("");
		classifiedList.querySelectorAll(".doc-tile-clickable, .doc-tile-icon[data-url]").forEach((el) => {
			el.addEventListener("click", () => window.open(el.dataset.url, "_blank"));
		});
		classifiedList.querySelectorAll(".btn-delete-doc").forEach((btn) => {
			btn.addEventListener("click", (e) => {
				e.stopPropagation();
				deleteDocument(c.id, parseInt(btn.dataset.docId));
			});
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
	const btnStop = $("#btn-stop");
	if (isProcessing) {
		btnProcess.style.display = "none";
		btnStop.style.display = "";
	} else {
		btnProcess.style.display = "";
		btnStop.style.display = "none";
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

		// Load extracted data summary
		loadExtractedData(c.id);
	} else {
		outputCard.style.display = "none";
		$("#extracted-card").style.display = "none";
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

	const fileList = Array.from(files);
	const total = fileList.length;
	let completed = 0;
	let failed = 0;

	const panel = $("#upload-progress-panel");
	const titleEl = $("#upload-progress-title");
	const summaryEl = $("#upload-progress-summary");
	const barEl = $("#upload-progress-bar");
	const listEl = $("#upload-file-list");
	const uploadZoneEl = $("#upload-zone");

	// Hide the drop zone, show progress panel
	uploadZoneEl.style.display = "none";
	panel.style.display = "";
	titleEl.textContent = `Uploading ${total} file${total > 1 ? "s" : ""}...`;
	summaryEl.textContent = `0 / ${total}`;
	barEl.style.width = "0%";

	// Build file item list
	listEl.innerHTML = fileList
		.map((f, i) => {
			const size = formatFileSize(f.size);
			return `<div class="upload-file-item pending" id="upload-item-${i}">
				<div class="upload-file-icon">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>
				</div>
				<span class="upload-file-name" title="${esc(f.name)}">${esc(f.name)}</span>
				<span class="upload-file-size">${size}</span>
				<span class="upload-file-status">Waiting</span>
			</div>`;
		})
		.join("");

	// Upload files one by one
	for (let i = 0; i < total; i++) {
		const file = fileList[i];
		const itemEl = $(`#upload-item-${i}`);

		// Mark current file as uploading
		itemEl.className = "upload-file-item uploading";
		itemEl.querySelector(".upload-file-icon").innerHTML =
			'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
		itemEl.querySelector(".upload-file-status").textContent = "Uploading...";
		titleEl.textContent = `Uploading: ${file.name}`;

		// Scroll the item into view
		itemEl.scrollIntoView({ behavior: "smooth", block: "nearest" });

		try {
			await uploadSingleFile(currentCaseId, file, (percent) => {
				// Update per-file progress in the overall bar
				const overallPercent = ((completed + percent / 100) / total) * 100;
				barEl.style.width = `${overallPercent.toFixed(1)}%`;
				itemEl.querySelector(".upload-file-status").textContent = `${Math.round(percent)}%`;
			});

			completed++;
			itemEl.className = "upload-file-item done";
			itemEl.querySelector(".upload-file-icon").innerHTML =
				'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>';
			itemEl.querySelector(".upload-file-status").textContent = "Done";
		} catch (err) {
			failed++;
			completed++;
			itemEl.className = "upload-file-item error";
			itemEl.querySelector(".upload-file-icon").innerHTML =
				'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 6L6 18M6 6l12 12"/></svg>';
			itemEl.querySelector(".upload-file-status").textContent = "Failed";
		}

		summaryEl.textContent = `${completed} / ${total}`;
		barEl.style.width = `${(completed / total) * 100}%`;
	}

	// Final state
	const successCount = completed - failed;
	if (failed === 0) {
		titleEl.textContent = `All ${total} file${total > 1 ? "s" : ""} uploaded successfully`;
	} else {
		titleEl.textContent = `${successCount} uploaded, ${failed} failed`;
	}

	// After a short delay, hide progress and restore upload zone
	setTimeout(() => {
		panel.style.display = "none";
		uploadZoneEl.style.display = "";
		openCase(currentCaseId);
	}, 1500);

	if (failed > 0) {
		toast(`${failed} file(s) failed to upload`, "error");
	} else {
		toast(`${successCount} file${successCount > 1 ? "s" : ""} uploaded`, "success");
	}
}

function uploadSingleFile(caseId, file, onProgress) {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		const fd = new FormData();
		fd.append("files", file);

		xhr.upload.addEventListener("progress", (e) => {
			if (e.lengthComputable) {
				onProgress((e.loaded / e.total) * 100);
			}
		});

		xhr.addEventListener("load", () => {
			if (xhr.status >= 200 && xhr.status < 300) {
				resolve(JSON.parse(xhr.responseText));
			} else {
				let msg = `HTTP ${xhr.status}`;
				try {
					const body = JSON.parse(xhr.responseText);
					if (body.detail) msg = body.detail;
				} catch {}
				reject(new Error(msg));
			}
		});

		xhr.addEventListener("error", () => reject(new Error("Network error")));
		xhr.addEventListener("abort", () => reject(new Error("Upload aborted")));

		xhr.open("POST", `${API}/api/cases/${caseId}/upload`);
		xhr.send(fd);
	});
}

function formatFileSize(bytes) {
	if (bytes < 1024) return bytes + " B";
	if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
	return (bytes / (1024 * 1024)).toFixed(1) + " MB";
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
		btnProcess.style.display = "none";
		const btnStop = $("#btn-stop");
		btnStop.style.display = "";

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

// ── Stop Processing ──────────────────────────────────────────────────────────
$("#btn-stop").addEventListener("click", async () => {
	if (!currentCaseId) return;
	const btnStop = $("#btn-stop");
	try {
		btnStop.disabled = true;
		btnStop.innerHTML = `
			<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
			Stopping...`;
		await api(`/api/cases/${currentCaseId}/stop`, { method: "POST" });
		toast("Stop signal sent, waiting for current operation to finish...", "info");
	} catch (err) {
		toast(err.message, "error");
		btnStop.disabled = false;
		btnStop.innerHTML = `
			<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
			Stop Processing`;
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

// ── Delete Document ──────────────────────────────────────────────────────────
async function deleteDocument(caseId, docId) {
	if (!confirm("Delete this document?")) return;
	try {
		await api(`/api/cases/${caseId}/documents/${docId}`, { method: "DELETE" });
		toast("Document deleted", "success");
		openCase(caseId);
	} catch (err) {
		toast(err.message, "error");
	}
}

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
		$("#ai-model").value = settings.ai_model || "";
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

$("#btn-save-model").addEventListener("click", async () => {
	const model = $("#ai-model").value;
	try {
		await api("/api/settings", {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ ai_model: model }),
		});
		toast("Model saved", "success");
	} catch (err) {
		toast(err.message, "error");
	}
});

// ── Extracted Data Display ────────────────────────────────────────────────────
const SECTION_LABELS = {
	insurance: { icon: "🛡️", title: "Insurance Policy" },
	rc: { icon: "🚗", title: "Registration Certificate" },
	dl: { icon: "🪪", title: "Driving License" },
	estimate: { icon: "🔧", title: "Repair Estimate" },
	invoice: { icon: "🧾", title: "Final Invoice" },
	route_permit: { icon: "📋", title: "Route Permit" },
	fitness_cert: { icon: "✅", title: "Fitness Certificate" },
};

const FIELD_LABELS = {
	insurer_name: "Insurer",
	insurer_address: "Insurer Address",
	policy_number: "Policy No.",
	policy_period: "Policy Period",
	idv: "IDV (₹)",
	insured_name: "Insured Name",
	insured_address: "Insured Address",
	contact_number: "Contact",
	hpa_with: "HPA With",
	registration_number: "Reg. No.",
	date_of_reg_issue: "Reg. Issue Date",
	date_of_reg_expiry: "Reg. Expiry",
	chassis_number: "Chassis No.",
	engine_number: "Engine No.",
	make_year: "Make/Year",
	body_type: "Body Type",
	vehicle_class: "Vehicle Class",
	laden_weight: "Laden Weight",
	unladen_weight: "Unladen Weight",
	seating_capacity: "Seats",
	fuel_type: "Fuel",
	colour: "Colour",
	road_tax_paid_upto: "Road Tax Upto",
	cubic_capacity: "CC",
	driver_name: "Driver Name",
	dob: "Date of Birth",
	address: "Address",
	city_state: "City/State",
	licence_number: "License No.",
	alt_licence_number: "Alt License No.",
	date_of_issue: "Issue Date",
	valid_till: "Valid Till",
	issuing_authority: "Authority",
	licence_type: "License Type",
	dealer_name: "Dealer",
	dealer_address: "Dealer Address",
	total_labour_estimated: "Total Labour (₹)",
	permit_no: "Permit No.",
	valid_upto: "Valid Upto",
	type_of_permit: "Permit Type",
	route_area: "Route/Area",
};

async function loadExtractedData(caseId) {
	const card = $("#extracted-card");
	const body = $("#extracted-body");
	try {
		const data = await api(`/api/cases/${caseId}/extracted`);
		let html = "";
		for (const [key, meta] of Object.entries(SECTION_LABELS)) {
			const section = data[key];
			if (!section) continue;
			html += renderSection(meta.icon, meta.title, section, key);
		}
		if (!html) {
			card.style.display = "none";
			return;
		}
		body.innerHTML = html;
		card.style.display = "";
	} catch {
		card.style.display = "none";
	}
}

function renderSection(icon, title, data, sectionKey) {
	let content = "";

	// Render key-value fields (skip arrays)
	const fields = Object.entries(data).filter(([k, v]) => !Array.isArray(v) && FIELD_LABELS[k]);
	if (fields.length > 0) {
		content += '<div class="ext-fields">';
		for (const [k, v] of fields) {
			const label = FIELD_LABELS[k] || k.replace(/_/g, " ");
			const isEmpty = v === "" || v === null || v === undefined;
			const displayVal = isEmpty ? "—" : typeof v === "number" ? v.toLocaleString("en-IN") : v;
			const emptyClass = isEmpty ? " ext-value-empty" : "";
			content += `<div class="ext-field"><span class="ext-label">${esc(label)}</span><span class="ext-value${emptyClass}">${esc(String(displayVal))}</span></div>`;
		}
		content += "</div>";
	}

	// Render parts table (for estimate)
	if (data.parts && data.parts.length > 0) {
		content += `<div class="ext-table-wrap"><table class="ext-table"><thead><tr><th>#</th><th>Part Name</th><th>Est. Price (₹)</th><th>Type</th></tr></thead><tbody>`;
		for (const p of data.parts) {
			content += `<tr><td>${p.sn || ""}</td><td>${esc(p.name)}</td><td class="num">${Number(p.estimated_price).toLocaleString("en-IN")}</td><td><span class="ext-cat ext-cat-${esc(p.category || "")}">${esc(p.category || "")}</span></td></tr>`;
		}
		content += "</tbody></table></div>";
	}

	// Render labour table (for estimate)
	if (data.labour && data.labour.length > 0) {
		content += `<div class="ext-table-wrap"><h4 class="ext-subtitle">Labour Breakdown</h4><table class="ext-table"><thead><tr><th>#</th><th>Description</th><th>R/R (₹)</th><th>Denting (₹)</th><th>C/W (₹)</th><th>Painting (₹)</th></tr></thead><tbody>`;
		for (const l of data.labour) {
			content += `<tr><td>${l.sn || ""}</td><td>${esc(l.description)}</td><td class="num">${Number(l.rr).toLocaleString("en-IN")}</td><td class="num">${Number(l.denting).toLocaleString("en-IN")}</td><td class="num">${Number(l.cw).toLocaleString("en-IN")}</td><td class="num">${Number(l.painting).toLocaleString("en-IN")}</td></tr>`;
		}
		content += "</tbody></table></div>";
	}

	// Render parts_assessed table (for invoice)
	if (data.parts_assessed && data.parts_assessed.length > 0) {
		content += `<div class="ext-table-wrap"><table class="ext-table"><thead><tr><th>#</th><th>Part Name</th><th>Assessed Price (₹)</th></tr></thead><tbody>`;
		data.parts_assessed.forEach((p, i) => {
			content += `<tr><td>${i + 1}</td><td>${esc(p.name)}</td><td class="num">${Number(p.assessed_price).toLocaleString("en-IN")}</td></tr>`;
		});
		content += "</tbody></table></div>";
	}

	if (!content) return "";

	return `<div class="ext-section">
		<div class="ext-section-header"><span class="ext-section-icon">${icon}</span><h3>${esc(title)}</h3></div>
		${content}
	</div>`;
}

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
