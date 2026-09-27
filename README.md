# Reelist

A poster-first movie recommender: search or browse a title, and see what's
similar in both **writing** (a local TF‑IDF model) and **genre** (TMDB).

```
project/
├── backend/            FastAPI service (your original main.py, lightly adapted)
│   ├── main.py
│   ├── requirements.txt
│   ├── .env.example
│   └── df.pkl, indices.pkl, tfidf_matrix.pkl, tfidf.pkl   ← add these yourself
├── frontend/            Static HTML/CSS/JS, no build step
│   ├── index.html
│   ├── css/styles.css
│   └── js/{config.js, app.js}
└── render.yaml          Render Blueprint for both services
```

## 1. Run it locally

Use the root Python 3.14 environment for both services. In PowerShell, start
the backend:

```bash
cd backend
..\.venv314\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

The API and interactive docs are at `http://127.0.0.1:8000` and
`http://127.0.0.1:8000/docs`.

In a second PowerShell terminal from the project root, start the frontend:

```powershell
.\.venv314\Scripts\python.exe -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Visit `http://127.0.0.1:5500`. The frontend API URL is configured in
`frontend/config.js`.

If TMDB is unavailable, the app automatically uses the local movie catalog
for browsing, search, and recommendations. Local catalog cards do not include
TMDB posters or release dates.

To enable TMDB data and posters, configure either `TMDB_ACCESS_TOKEN` (the
TMDB API Read Access Token, sent as a Bearer token) or `TMDB_API_KEY` (the
TMDB API Key, v3 auth) in `backend/.env`, then restart the backend. If both
are set, the access token is used.

## 2. Deploy to Render

The included `render.yaml` defines two services — the API and the static
site — as a single Blueprint.

1. Push this project to a Git repo (GitHub/GitLab).
2. Make sure your four pickle files are committed inside `backend/` (or, for
   larger files, fetched at startup from S3/GCS — `render.yaml` doesn't do
   that for you).
3. In the Render dashboard: **New → Blueprint**, point it at the repo. Render
   will read `render.yaml` and propose both services.
4. Before the first deploy finishes, set the backend's `TMDB_API_KEY`
   environment variable (Render dashboard → `reelist-api` → Environment) —
   it's marked `sync: false` in the blueprint so it isn't stored in Git.
5. Once `reelist-api` is live, copy its URL (e.g.
   `https://reelist-api.onrender.com`) into
   `frontend/js/config.js` → `API_BASE_URL`, commit, and push. Render
   redeploys the static site automatically.
6. Optional but recommended: set the backend's `ALLOWED_ORIGINS` env var to
   your frontend's exact URL instead of `*`, once you know it.

No Blueprint? You can create the two services by hand instead:

- **Web Service** (`backend/`): build `pip install -r requirements.txt`,
  start `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- **Static Site** (`frontend/`): no build command, publish directory `.`.

## Notes

- The frontend is plain HTML/CSS/JS — no bundler, no framework, no
  `localStorage`/`sessionStorage` dependency, so it works as a static site
  anywhere (Render, Netlify, GitHub Pages, S3).
- All backend calls go through the endpoints already in `main.py`
  (`/home`, `/tmdb/search`, `/movie/search`, `/recommend/*`); nothing on the
  frontend talks to TMDB directly, so your TMDB key never reaches the
  browser.
- `render.yaml`'s free-tier web service will spin down after inactivity;
  the first request after idling can take ~30–50s to wake it up.
