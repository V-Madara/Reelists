<div align="center">

# 🎬 Reelist

### A poster-first movie recommender

Search or browse a title and discover what's similar — in **writing style** *and* **genre**.

<a href="https://reelists-1.onrender.com/">
  <img alt="Live Demo" src="https://img.shields.io/badge/🔴_Live_Demo-Visit_Site-e50914?style=for-the-badge">
</a>

<br><br>

<img alt="Python" src="https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python&logoColor=white">
<img alt="FastAPI" src="https://img.shields.io/badge/Backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white">
<img alt="Frontend" src="https://img.shields.io/badge/Frontend-HTML%2FCSS%2FJS-E34F26?style=flat-square&logo=html5&logoColor=white">
<img alt="TMDB" src="https://img.shields.io/badge/Data-TMDB-01D277?style=flat-square&logo=themoviedatabase&logoColor=white">
<img alt="Deploy" src="https://img.shields.io/badge/Deploy-Render-46E3B7?style=flat-square&logo=render&logoColor=white">
<img alt="License" src="https://img.shields.io/badge/No%20Build%20Step-Vanilla%20JS-yellow?style=flat-square">

</div>

---

## ✨ What is Reelist?

Reelist blends **two** recommendation signals into a single, clean, poster-driven interface — no sign-up, no bloat, just movies you'll actually like.

<div align="center">

| Signal | Powered by | Captures |
|:---:|:---:|:---|
| 📝 **Writing similarity** | Local TF-IDF model | Plot, tone & thematic overlap |
| 🎭 **Genre similarity** | TMDB API | Category & audience overlap |

</div>

> 💡 **Graceful fallback:** if TMDB is ever unreachable, Reelist automatically switches to a local movie catalog for browsing, search, and recommendations — just without posters or release dates.

---

## 📁 Project Structure

```
project/
├── backend/                 FastAPI service
│   ├── main.py
│   ├── requirements.txt
│   ├── .env.example
│   └── df.pkl, indices.pkl, tfidf_matrix.pkl, tfidf.pkl   ← add these yourself
├── frontend/                 Static HTML/CSS/JS — zero build step
│   ├── index.html
│   ├── css/styles.css
│   └── js/{config.js, app.js}
└── render.yaml               Render Blueprint for both services
```

---

## 🚀 Quickstart — Run Locally

Use the root Python 3.14 environment for both services.

**① Start the backend** *(PowerShell, from `backend/`)*

```powershell
cd backend
..\.venv314\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

📍 API live at `http://127.0.0.1:8000` · Interactive docs at `http://127.0.0.1:8000/docs`

**② Start the frontend** *(second terminal, from the project root)*

```powershell
.\.venv314\Scripts\python.exe -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

📍 Visit `http://127.0.0.1:5500` — the API URL is configured in `frontend/config.js`

### 🔑 Unlocking TMDB posters & live data

Add **one** of these to `backend/.env`, then restart the backend:

```env
TMDB_ACCESS_TOKEN=your_read_access_token   # preferred — used first if both are set
TMDB_API_KEY=your_v3_api_key
```

---

## ☁️ Deploying to Render

`render.yaml` defines two services — the API and the static site — as a single **Blueprint**.

```
①  Push this project to a Git repo (GitHub / GitLab)
②  Commit your 4 pickle files inside backend/
      (or fetch larger ones from S3/GCS at startup)
③  Render Dashboard → New → Blueprint → point at your repo
④  Set TMDB_API_KEY on "reelist-api" → Environment
      (marked sync: false — never stored in Git)
⑤  Copy the live API URL into frontend/js/config.js → API_BASE_URL
      → commit → push → static site auto-redeploys
⑥  (Recommended) Lock ALLOWED_ORIGINS to your frontend's
      exact URL instead of "*"
```

**No Blueprint? Set services up by hand:**

| Service | Type | Build Command | Start Command |
|---|---|---|---|
| `backend/` | Web Service | `pip install -r requirements.txt` | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| `frontend/` | Static Site | *(none)* | Publish directory: `.` |

---

## 📝 Good to Know

- 🧩 The frontend is plain HTML/CSS/JS — no bundler, no framework, no `localStorage`/`sessionStorage` dependency — so it deploys as a static site anywhere (Render, Netlify, GitHub Pages, S3).
- 🔒 All backend calls route through `main.py` (`/home`, `/tmdb/search`, `/movie/search`, `/recommend/*`) — nothing on the frontend talks to TMDB directly, so your API key never reaches the browser.
- ⏳ Render's free-tier web service spins down after inactivity — the first request after idling can take **~30–50s** to wake it back up.

---

<div align="center">

### 🎞️ Made with passion for cinema

**Created by — Vishal Prajapati**

</div>
