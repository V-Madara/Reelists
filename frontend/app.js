/* Reelist — frontend application logic
 * Talks to the FastAPI backend defined in js/config.js.
 * No build step, no framework: plain fetch + DOM.
 */
(() => {
  "use strict";

  const API_BASE = (window.REELIST_CONFIG && window.REELIST_CONFIG.API_BASE_URL) || "";
  const PLACEHOLDER_POSTER =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns='http://www.w3.org/2000/svg' width='300' height='450'>
         <rect width='100%' height='100%' fill='#262229'/>
         <text x='50%' y='50%' fill='#6f6874' font-family='sans-serif' font-size='16'
               text-anchor='middle' dominant-baseline='middle'>No image</text>
       </svg>`
    );

  const ROWS = [
    { key: "trending", title: "Trending today", category: "trending" },
    { key: "popular", title: "Popular right now", category: "popular" },
    { key: "top_rated", title: "Critically acclaimed", category: "top_rated" },
    { key: "upcoming", title: "Coming soon", category: "upcoming" },
  ];

  const rowsEl = document.getElementById("rows");
  const statusBanner = document.getElementById("statusBanner");
  const heroEl = document.getElementById("hero");
  const heroTitleEl = document.getElementById("heroTitle");
  const heroOverviewEl = document.getElementById("heroOverview");
  const heroDetailsBtn = document.getElementById("heroDetailsBtn");
  const searchForm = document.getElementById("searchForm");
  const searchInput = document.getElementById("searchInput");
  const searchSuggestions = document.getElementById("searchSuggestions");
  const modalBackdrop = document.getElementById("modalBackdrop");
  const modalContent = document.getElementById("modalContent");
  const modalClose = document.getElementById("modalClose");

  let heroQuery = null;

  // ---------- fetch helper ----------
  async function api(path) {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) {
        /* ignore parse errors */
      }
      throw new Error(detail);
    }
    return res.json();
  }

  function showStatus(message) {
    if (!message) {
      statusBanner.hidden = true;
      statusBanner.textContent = "";
      return;
    }
    statusBanner.hidden = false;
    statusBanner.textContent = message;
  }

  function posterSkeletonRow(count) {
    return Array.from({ length: count })
      .map(() => `<div class="poster-card poster-skeleton"><div class="poster-img-wrap"></div></div>`)
      .join("");
  }

  // ---------- rows ----------
  function renderRow(rowKey, title, cards) {
    const track = cards
      .map(
        (c) => `
        <button class="poster-card" data-query="${escapeAttr(c.title)}">
          <div class="poster-img-wrap">
            <img src="${c.poster_url || PLACEHOLDER_POSTER}" alt="${escapeAttr(c.title)}" loading="lazy" />
          </div>
          <div class="poster-meta">
            <p class="poster-title">${escapeHtml(c.title)}</p>
            <p class="poster-sub">${yearOf(c.release_date)} ${c.vote_average ? "· ★ " + c.vote_average.toFixed(1) : ""}</p>
          </div>
        </button>`
      )
      .join("");

    const section = document.createElement("section");
    section.className = "row";
    section.dataset.row = rowKey;
    section.innerHTML = `
      <h2 class="row-heading">${escapeHtml(title)}</h2>
      <div class="row-track">${track || "<p class='status-banner'>Nothing to show here yet.</p>"}</div>
    `;
    return section;
  }

  async function loadHomeRows() {
    // Skeletons first so the page never looks empty while TMDB responds.
    ROWS.forEach((row) => {
      const section = document.createElement("section");
      section.className = "row";
      section.dataset.row = row.key;
      section.innerHTML = `
        <h2 class="row-heading">${escapeHtml(row.title)}</h2>
        <div class="row-track">${posterSkeletonRow(8)}</div>
      `;
      rowsEl.appendChild(section);
    });

    const results = await Promise.allSettled(
      ROWS.map((row) => api(`/home?category=${row.category}&limit=20`))
    );

    let anyFailed = false;
    let heroCandidate = null;

    results.forEach((result, i) => {
      const row = ROWS[i];
      const target = rowsEl.querySelector(`section[data-row="${row.key}"]`);
      if (result.status === "fulfilled") {
        const cards = result.value;
        if (!heroCandidate && cards.length) heroCandidate = cards[0];
        target.replaceWith(renderRow(row.key, row.title, cards));
      } else {
        anyFailed = true;
        target.querySelector(".row-track").innerHTML =
          "<p class='status-banner' style='margin:0;'>Couldn't load this row.</p>";
      }
    });

    if (anyFailed) {
      showStatus("Some rows couldn't load — check that the backend and TMDB key are reachable.");
    }

    if (heroCandidate) setHero(heroCandidate);
  }

  function setHero(card) {
    heroQuery = card.title;
    heroEl.style.setProperty(
      "background-image",
      card.poster_url ? `url("${card.poster_url}")` : "none"
    );
    heroTitleEl.textContent = card.title;
    heroOverviewEl.textContent = card.release_date
      ? `Released ${yearOf(card.release_date)}`
      : "";
  }

  heroDetailsBtn.addEventListener("click", () => {
    if (heroQuery) openMovieModal(heroQuery);
  });

  // ---------- delegated poster clicks ----------
  rowsEl.addEventListener("click", (e) => {
    const card = e.target.closest(".poster-card[data-query]");
    if (card) openMovieModal(card.dataset.query);
  });

  // ---------- search ----------
  let searchDebounce = null;
  let activeSearchController = null;

  searchInput.addEventListener("input", () => {
    const q = searchInput.value.trim();
    clearTimeout(searchDebounce);
    if (q.length < 2) {
      hideSuggestions();
      return;
    }
    searchDebounce = setTimeout(() => runSearch(q), 280);
  });

  searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = searchInput.value.trim();
    if (q) {
      hideSuggestions();
      openMovieModal(q);
    }
  });

  document.addEventListener("click", (e) => {
    if (!searchForm.contains(e.target)) hideSuggestions();
  });

  async function runSearch(query) {
    if (activeSearchController) activeSearchController.abort();
    activeSearchController = new AbortController();
    try {
      const res = await fetch(
        `${API_BASE}/tmdb/search?query=${encodeURIComponent(query)}&page=1`,
        { signal: activeSearchController.signal }
      );
      if (!res.ok) throw new Error("search failed");
      const data = await res.json();
      renderSuggestions((data.results || []).slice(0, 7));
    } catch (err) {
      if (err.name !== "AbortError") hideSuggestions();
    }
  }

  function renderSuggestions(results) {
    if (!results.length) {
      searchSuggestions.innerHTML = `<li class="s-empty">No titles match “${escapeHtml(searchInput.value)}”.</li>`;
      searchSuggestions.hidden = false;
      return;
    }
    searchSuggestions.innerHTML = results
      .map(
        (m) => `
        <li>
          <button type="button" data-query="${escapeAttr(m.title)}">
            <img src="${m.poster_path ? "https://image.tmdb.org/t/p/w92" + m.poster_path : PLACEHOLDER_POSTER}" alt="" />
            <span class="s-meta">
              <span class="s-title">${escapeHtml(m.title)}</span><br/>
              <span class="s-year">${yearOf(m.release_date)}</span>
            </span>
          </button>
        </li>`
      )
      .join("");
    searchSuggestions.hidden = false;
  }

  function hideSuggestions() {
    searchSuggestions.hidden = true;
  }

  searchSuggestions.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-query]");
    if (!btn) return;
    searchInput.value = btn.dataset.query;
    hideSuggestions();
    openMovieModal(btn.dataset.query);
  });

  // ---------- modal / details ----------
  async function openMovieModal(query) {
    modalBackdrop.hidden = false;
    document.body.style.overflow = "hidden";
    modalContent.innerHTML = `<p class="modal-loading">Loading “${escapeHtml(query)}”…</p>`;

    try {
      const bundle = await api(`/movie/search?query=${encodeURIComponent(query)}`);
      modalContent.innerHTML = renderModal(bundle);
    } catch (err) {
      modalContent.innerHTML = `<p class="modal-error">Couldn't load that title: ${escapeHtml(err.message)}</p>`;
    }
  }

  function renderModal(bundle) {
    const d = bundle.movie_details;
    const genrePills = (d.genres || [])
      .map((g) => `<span class="genre-pill">${escapeHtml(g.name)}</span>`)
      .join("");

    const tfidfCards = (bundle.tfidf_recommendations || [])
      .map((item) => {
        const t = item.tmdb;
        const sub = item.from_catalog === false
          ? `${yearOf(t && t.release_date)} ${t && t.vote_average ? "· ★ " + t.vote_average.toFixed(1) : ""}`
          : `match ${(item.score * 100).toFixed(0)}%`;
        return `
          <button class="poster-card" data-query="${escapeAttr(item.title)}">
            <div class="poster-img-wrap">
              <img src="${(t && t.poster_url) || PLACEHOLDER_POSTER}" alt="${escapeAttr(item.title)}" loading="lazy" />
            </div>
            <div class="poster-meta">
              <p class="poster-title">${escapeHtml(item.title)}</p>
              <p class="poster-sub poster-score">${sub}</p>
            </div>
          </button>`;
      })
      .join("");

    const genreCards = (bundle.genre_recommendations || [])
      .map(
        (c) => `
        <button class="poster-card" data-query="${escapeAttr(c.title)}">
          <div class="poster-img-wrap">
            <img src="${c.poster_url || PLACEHOLDER_POSTER}" alt="${escapeAttr(c.title)}" loading="lazy" />
          </div>
          <div class="poster-meta">
            <p class="poster-title">${escapeHtml(c.title)}</p>
            <p class="poster-sub">${yearOf(c.release_date)} ${c.vote_average ? "· ★ " + c.vote_average.toFixed(1) : ""}</p>
          </div>
        </button>`
      )
      .join("");

    return `
      <div class="modal-header">
        <div class="modal-poster">
          <img src="${d.poster_url || PLACEHOLDER_POSTER}" alt="${escapeAttr(d.title)}" />
        </div>
        <div class="modal-title-block">
          <h2 id="modalTitle">${escapeHtml(d.title)}</h2>
          <p class="modal-meta">${yearOf(d.release_date)}</p>
          <div class="modal-genres">${genrePills}</div>
          <p class="modal-overview">${escapeHtml(d.overview || "No synopsis available.")}</p>
        </div>
      </div>

      <div class="modal-section">
        <h3>Similar in tone and story</h3>
        <div class="row-track">${tfidfCards || "<p class='status-banner' style='margin:0;'>No local matches for this title yet.</p>"}</div>
      </div>

      <div class="modal-section">
        <h3>More ${escapeHtml((d.genres && d.genres[0] && d.genres[0].name) || "")}</h3>
        <div class="row-track">${genreCards || "<p class='status-banner' style='margin:0;'>Nothing else to show.</p>"}</div>
      </div>
    `;
  }

  modalContent.addEventListener("click", (e) => {
    const card = e.target.closest(".poster-card[data-query]");
    if (card) openMovieModal(card.dataset.query);
  });

  function closeModal() {
    modalBackdrop.hidden = true;
    document.body.style.overflow = "";
  }

  modalClose.addEventListener("click", closeModal);
  modalBackdrop.addEventListener("click", (e) => {
    if (e.target === modalBackdrop) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modalBackdrop.hidden) closeModal();
  });

  // ---------- utils ----------
  function yearOf(dateStr) {
    return dateStr ? dateStr.slice(0, 4) : "—";
  }

  function escapeHtml(str) {
    return String(str || "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function escapeAttr(str) {
    return escapeHtml(str);
  }

  // ---------- boot ----------
  async function boot() {
    await loadHomeRows();
    const health = await api("/health");
    if (!health.tmdb_available) {
      const upcomingHeading = rowsEl.querySelector('[data-row="upcoming"] .row-heading');
      if (upcomingHeading) upcomingHeading.textContent = "More from the local catalog";
      showStatus(
        health.tmdb_configured
          ? "TMDB is unreachable; showing recommendations from the local movie catalog."
          : "TMDB is not configured; showing recommendations from the local movie catalog."
      );
    }
  }

  boot().catch((err) => {
    showStatus(`Couldn't reach the backend at ${API_BASE || "(same origin)"}: ${err.message}`);
  });
})();
