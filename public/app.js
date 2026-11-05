/**
 * Frontend do AI Data Aggregator.
 * Faz fetch direto de `data/data.json`, popula o DOM de forma semântica e
 * disponibiliza visualizações em Cards, Texto Puro (Markdown/LLM) e JSON Bruto.
 */

const state = {
  rawData: null,
  searchTerm: "",
  selectedCategory: "all",
  currentView: "cards",
};

const elements = {
  projectTitle: document.getElementById("project-title"),
  projectDescription: document.getElementById("project-description"),
  statUpdated: document.getElementById("stat-updated"),
  statSources: document.getElementById("stat-sources"),
  statItems: document.getElementById("stat-items"),
  searchInput: document.getElementById("search-input"),
  categoryFilter: document.getElementById("category-filter"),
  sourcesContainer: document.getElementById("sources-container"),
  markdownOutput: document.getElementById("markdown-output"),
  jsonOutput: document.getElementById("json-output"),
  structuredScript: document.getElementById("ai-agent-structured-data"),
  viewButtons: document.querySelectorAll(".btn-view"),
  views: {
    cards: document.getElementById("view-cards"),
    markdown: document.getElementById("view-markdown"),
    json: document.getElementById("view-json"),
  },
  copyMarkdownBtn: document.getElementById("copy-markdown-btn"),
  copyJsonBtn: document.getElementById("copy-json-btn"),
};

async function init() {
  bindEvents();
  try {
    const response = await fetch("data/data.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} ao buscar data/data.json`);
    }
    const data = await response.json();
    state.rawData = data;

    if (elements.structuredScript) {
      elements.structuredScript.textContent = JSON.stringify(data);
    }

    populateMetadata(data.metadata || {});
    populateCategoryOptions(data.sources || []);
    renderAll();
  } catch (error) {
    elements.sourcesContainer.innerHTML = `
      <div class="source-block" style="padding: 1.25rem;">
        <p class="status-error"><strong>Erro ao carregar dados:</strong> ${escapeHtml(error.message)}</p>
        <p style="margin-top: 0.5rem; color: var(--text-muted);">
          Execute <code>python -m src.main</code> para gerar o arquivo <code>public/data/data.json</code>.
        </p>
      </div>
    `;
  }
}

function bindEvents() {
  elements.searchInput.addEventListener("input", (e) => {
    state.searchTerm = e.target.value.trim().toLowerCase();
    renderAll();
  });

  elements.categoryFilter.addEventListener("change", (e) => {
    state.selectedCategory = e.target.value;
    renderAll();
  });

  elements.viewButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetView = btn.dataset.view;
      state.currentView = targetView;
      elements.viewButtons.forEach((b) => b.classList.toggle("active", b === btn));
      Object.entries(elements.views).forEach(([key, section]) => {
        section.classList.toggle("hidden", key !== targetView);
      });
    });
  });

  elements.copyMarkdownBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(elements.markdownOutput.textContent);
    elements.copyMarkdownBtn.textContent = "Copiado!";
    setTimeout(() => (elements.copyMarkdownBtn.textContent = "Copiar Markdown"), 1500);
  });

  elements.copyJsonBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(elements.jsonOutput.textContent);
    elements.copyJsonBtn.textContent = "Copiado!";
    setTimeout(() => (elements.copyJsonBtn.textContent = "Copiar JSON"), 1500);
  });
}

function populateMetadata(meta) {
  if (meta.project_name) elements.projectTitle.textContent = meta.project_name;
  if (meta.description) elements.projectDescription.textContent = meta.description;
  elements.statUpdated.textContent = meta.generated_at_utc || "—";
  elements.statSources.textContent = String(meta.total_sources ?? 0);
  elements.statItems.textContent = String(meta.total_items ?? 0);
}

function populateCategoryOptions(sources) {
  const categories = Array.from(new Set(sources.map((s) => s.category).filter(Boolean)));
  categories.forEach((category) => {
    const opt = document.createElement("option");
    opt.value = category;
    opt.textContent = category;
    elements.categoryFilter.appendChild(opt);
  });
}

function getFilteredSources() {
  if (!state.rawData || !Array.isArray(state.rawData.sources)) return [];

  return state.rawData.sources
    .filter((source) => state.selectedCategory === "all" || source.category === state.selectedCategory)
    .map((source) => {
      if (!state.searchTerm) return source;
      const sourceMatches =
        (source.name && source.name.toLowerCase().includes(state.searchTerm)) ||
        (source.category && source.category.toLowerCase().includes(state.searchTerm));

      if (sourceMatches) return source;

      const filteredItems = (source.items || []).filter((item) =>
        JSON.stringify(item).toLowerCase().includes(state.searchTerm)
      );
      return {
        ...source,
        items_count: filteredItems.length,
        items: filteredItems,
      };
    })
    .filter((source) => source.items.length > 0 || !state.searchTerm);
}

function renderAll() {
  const filteredSources = getFilteredSources();
  renderCards(filteredSources);
  renderMarkdown(filteredSources);
  renderJson(filteredSources);
}

function renderCards(sources) {
  if (!sources.length) {
    elements.sourcesContainer.innerHTML = `<p class="loading-state">Nenhum resultado encontrado para o filtro atual.</p>`;
    return;
  }

  elements.sourcesContainer.innerHTML = sources
    .map((source) => {
      const statusClass = source.status === "success" ? "status-success" : "status-error";
      const itemsHtml = (source.items || [])
        .map((item, idx) => renderItemCard(item, idx))
        .join("");

      return `
        <article class="source-block" data-source-id="${escapeHtml(source.id)}" data-category="${escapeHtml(source.category)}">
          <header class="source-header">
            <div>
              <h2>${escapeHtml(source.name)}</h2>
              <small style="color: var(--text-muted);">${escapeHtml(source.source_url || "")}</small>
            </div>
            <div class="source-meta">
              <span class="tag">${escapeHtml((source.type || "source").toUpperCase())}</span>
              <span class="tag">${escapeHtml(source.category || "general")}</span>
              <span class="tag ${statusClass}">${escapeHtml(source.status)} (${source.items_count || 0})</span>
            </div>
          </header>
          <ul class="items-list">
            ${itemsHtml || `<li class="item-card">Nenhum item retornado.</li>`}
          </ul>
        </article>
      `;
    })
    .join("");
}

function renderItemCard(item, idx) {
  const title = item.title || item.name || `Registro #${idx + 1}`;
  const link = item.link || item.url;
  const titleHtml = link
    ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`
    : escapeHtml(title);

  const extraFields = Object.entries(item)
    .filter(([key, val]) => !["title", "name", "link", "url"].includes(key) && val !== null && val !== "")
    .map(([key, val]) => {
      const formattedVal = typeof val === "object" ? JSON.stringify(val) : String(val);
      return `<div class="item-field"><strong>${escapeHtml(key)}:</strong> ${escapeHtml(formattedVal)}</div>`;
    })
    .join("");

  return `
    <li class="item-card">
      <h3 class="item-title">${titleHtml}</h3>
      ${extraFields ? `<div class="item-fields">${extraFields}</div>` : ""}
    </li>
  `;
}

function renderMarkdown(sources) {
  const meta = state.rawData?.metadata || {};
  const lines = [
    `# ${meta.project_name || "AI Data Aggregator"}`,
    `> Gerado em: ${meta.generated_at_utc || "—"}`,
    "",
  ];

  sources.forEach((source) => {
    lines.push(`## [${(source.type || "SOURCE").toUpperCase()}] ${source.name} (${source.category})`);
    lines.push(`Fonte: ${source.source_url || "N/A"} | Status: ${source.status} | Itens: ${source.items_count}`);
    lines.push("");
    (source.items || []).forEach((item, i) => {
      const title = item.title || item.name || `Item ${i + 1}`;
      const url = item.link || item.url;
      lines.push(url ? `### ${i + 1}. [${title}](${url})` : `### ${i + 1}. ${title}`);
      Object.entries(item).forEach(([k, v]) => {
        if (!["title", "name", "link", "url"].includes(k) && v !== null && v !== "") {
          lines.push(`- **${k}**: ${typeof v === "object" ? JSON.stringify(v) : v}`);
        }
      });
      lines.push("");
    });
  });

  elements.markdownOutput.textContent = lines.join("\n");
}

function renderJson(sources) {
  const filteredPayload = {
    ...state.rawData,
    sources,
  };
  elements.jsonOutput.textContent = JSON.stringify(filteredPayload, null, 2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", init);
