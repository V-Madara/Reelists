// Reelist frontend config
//
// Point this at your deployed FastAPI backend.
// Locally (uvicorn main:app --reload) that's http://127.0.0.1:8000.
// After deploying the backend on Render, replace it with that
// service's URL, e.g. https://reelist-api.onrender.com
window.REELIST_CONFIG = {
  API_BASE_URL:"https://reelists.onrender.com",
};
